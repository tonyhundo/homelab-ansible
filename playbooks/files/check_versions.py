#!/usr/bin/env python3
"""Homelab version-compliance checker.

Polls each homelab service for its running version and compares against
endoflife.date (or GitHub releases as a fallback) to flag outdated and
EOL software. Writes a node_exporter textfile-collector .prom file.

Output metrics (one series per service):
    homelab_service_running_version{service,version} 1
    homelab_service_latest_patch{service,version} 1
    homelab_service_latest_major{service,version} 1
    homelab_service_supported{service} 1|0|-1
    homelab_service_eol_seconds{service} <int or -1>
    homelab_service_outdated_patch{service} 1|0
    homelab_service_outdated_major{service} 1|0
    homelab_service_check_timestamp <unix epoch>
"""

import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

OUTPUT_PATH = os.environ.get(
    "HOMELAB_VERSIONS_PROM",
    "/var/lib/node_exporter/textfile/homelab_versions.prom",
)
HTTP_TIMEOUT = 6
UNVERIFIED_CTX = ssl._create_unverified_context()


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "homelab-version-check/1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=UNVERIFIED_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def http_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "homelab-version-check/1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=UNVERIFIED_CTX) as r:
        return r.read().decode("utf-8")


def strip_v(s):
    return s.lstrip("vV").strip() if s else s


def parse_semver(v):
    """Return (major, minor, patch) tuple or None."""
    if not v:
        return None
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", strip_v(v))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def branch_of(v):
    """Return 'major.minor' string for cycle matching."""
    p = parse_semver(v)
    return f"{p[0]}.{p[1]}" if p else None


# --- Running-version probes ----------------------------------------------------

def running_grafana():
    return http_json("https://grafana.example.com:3000/api/health").get("version")


def running_prometheus():
    d = http_json("https://prometheus.example.com:9090/api/v1/status/buildinfo")
    return d.get("data", {}).get("version")


def running_vault():
    return http_json("https://vault.example.com:8200/v1/sys/health").get("version")


def running_pihole():
    d = http_json("http://pihole.example.com/api/info/version")
    return strip_v(d.get("version", {}).get("core", {}).get("local", {}).get("version"))


def running_stepca():
    return http_json("https://stepca.example.com:8443/version").get("version")


def running_gitea_prom():
    """Pull from Prometheus rather than hitting Gitea directly (already scraped)."""
    d = http_json(
        "https://prometheus.example.com:9090/api/v1/query?query=gitea_build_info"
    )
    res = d["data"]["result"]
    return res[0]["metric"].get("version") if res else None


def running_mariadb_prom():
    d = http_json(
        "https://prometheus.example.com:9090/api/v1/query?query=mysql_version_info"
    )
    res = d["data"]["result"]
    if not res:
        return None
    v = res[0]["metric"].get("version", "")
    m = re.match(r"^(\d+\.\d+\.\d+)", v)
    return m.group(1) if m else v


def running_proxmox_prom():
    d = http_json(
        "https://prometheus.example.com:9090/api/v1/query?query=pve_version_info"
    )
    res = d["data"]["result"]
    return res[0]["metric"].get("version") if res else None


def running_app_version_prom(service):
    """Read a host-published version metric back from Prometheus.

    Used for apps whose port is not reachable from the Prometheus host
    (vaultwarden, n8n, docmost); their host emits homelab_app_running_version
    via node_exporter's textfile collector (see app_version_emitter.yml).
    """
    query = urllib.parse.quote(f'homelab_app_running_version{{service="{service}"}}')
    d = http_json(
        f"https://prometheus.example.com:9090/api/v1/query?query={query}"
    )
    res = d["data"]["result"]
    if not res:
        return None
    version = res[0]["metric"].get("version")
    return version if version and version != "unknown" else None


# --- Reference (latest version) sources ---------------------------------------

def eol_lookup(product, running_version):
    """Return dict with latest_patch, latest_major, supported, eol_seconds.

    Matches the running version's branch to a cycle in endoflife.date.
    """
    out = {
        "latest_patch": None,
        "latest_major": None,
        "supported": -1,
        "eol_seconds": -1,
    }
    try:
        data = http_json(f"https://endoflife.date/api/{product}.json")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return out
    if not isinstance(data, list) or not data:
        return out

    # Latest major = first entry (endoflife.date orders newest first).
    out["latest_major"] = strip_v(str(data[0].get("latest") or data[0].get("cycle") or ""))

    branch = branch_of(running_version)
    cycle_match = None
    if branch:
        for entry in data:
            cycle = str(entry.get("cycle", ""))
            if cycle == branch or cycle.startswith(branch + "."):
                cycle_match = entry
                break
        # Fall back to major-only match if branch (major.minor) didn't hit.
        if not cycle_match:
            major = branch.split(".")[0]
            for entry in data:
                if str(entry.get("cycle", "")).split(".")[0] == major:
                    cycle_match = entry
                    break

    if cycle_match:
        out["latest_patch"] = strip_v(str(cycle_match.get("latest") or ""))
        support = cycle_match.get("support")
        eol = cycle_match.get("eol")
        # endoflife.date: support/eol can be bool (still supported / never EOL) or date string.
        if isinstance(support, bool):
            out["supported"] = 1 if support else 0
        elif support is False:
            out["supported"] = 0
        if isinstance(eol, str):
            try:
                eol_dt = datetime.fromisoformat(eol).replace(tzinfo=timezone.utc)
                delta = eol_dt - datetime.now(timezone.utc)
                out["eol_seconds"] = int(delta.total_seconds())
                if out["supported"] == -1:
                    out["supported"] = 1 if delta.total_seconds() > 0 else 0
            except ValueError:
                pass
        elif eol is False:
            out["eol_seconds"] = 10 * 365 * 86400  # treat "no EOL" as far future
            if out["supported"] == -1:
                out["supported"] = 1
        elif eol is True:
            out["supported"] = 0
    return out


def github_latest(repo, tag_strip_prefix=None):
    """Return latest release tag from GitHub for owner/repo, stripped of v/prefix."""
    try:
        data = http_json(f"https://api.github.com/repos/{repo}/releases/latest")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    tag = data.get("tag_name") or ""
    if tag_strip_prefix and tag.startswith(tag_strip_prefix):
        tag = tag[len(tag_strip_prefix):]
    return strip_v(tag)


def github_lookup(repo, running_version, tag_strip_prefix=None):
    """GitHub fallback when endoflife.date doesn't cover this product."""
    latest = github_latest(repo, tag_strip_prefix=tag_strip_prefix)
    out = {
        "latest_patch": latest,  # GitHub only gives us one number; treat as both
        "latest_major": latest,
        "supported": -1,
        "eol_seconds": -1,
    }
    return out


# --- Service catalogue --------------------------------------------------------

SERVICES = [
    # (service_name, instance_label, running_fn, reference_fn)
    ("gitea",       "gitea",       running_gitea_prom,    lambda v: github_lookup("go-gitea/gitea", v)),
    ("mariadb",     "mariadb",     running_mariadb_prom,  lambda v: eol_lookup("mariadb", v)),
    ("proxmox",     "chinstrap",   running_proxmox_prom,  lambda v: eol_lookup("proxmox-ve", v)),
    ("vault",       "vault",       running_vault,         lambda v: eol_lookup("hashicorp-vault", v)),
    ("grafana",     "grafana",     running_grafana,       lambda v: eol_lookup("grafana", v)),
    ("prometheus",  "prometheus",  running_prometheus,    lambda v: eol_lookup("prometheus", v)),
    ("pihole",      "pihole",      running_pihole,        lambda v: github_lookup("pi-hole/pi-hole", v)),
    ("stepca",      "stepca",      running_stepca,        lambda v: github_lookup("smallstep/certificates", v, tag_strip_prefix="v")),
    # Apps unreachable from the Prometheus host: running version published by
    # their own host via node_exporter textfile (see app_version_emitter.yml).
    ("vaultwarden", "vaultwarden", lambda: running_app_version_prom("vaultwarden"), lambda v: github_lookup("dani-garcia/vaultwarden", v)),
    ("n8n",         "n8n",         lambda: running_app_version_prom("n8n"),         lambda v: github_lookup("n8n-io/n8n", v, tag_strip_prefix="n8n@")),
    ("docmost",     "docmost",     lambda: running_app_version_prom("docmost"),     lambda v: github_lookup("docmost/docmost", v, tag_strip_prefix="v")),
]


# --- Output -------------------------------------------------------------------

def escape_label(v):
    return str(v).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ")


def emit_info(lines, metric, labels, value=1):
    label_str = ",".join(f'{k}="{escape_label(v)}"' for k, v in labels.items())
    lines.append(f"{metric}{{{label_str}}} {value}")


def main():
    lines = []
    lines.append("# HELP homelab_service_running_version Running version of homelab service")
    lines.append("# TYPE homelab_service_running_version gauge")
    lines.append("# HELP homelab_service_latest_patch Latest patch for the current branch")
    lines.append("# TYPE homelab_service_latest_patch gauge")
    lines.append("# HELP homelab_service_latest_major Latest major version available")
    lines.append("# TYPE homelab_service_latest_major gauge")
    lines.append("# HELP homelab_service_supported 1=supported,0=EOL,-1=unknown")
    lines.append("# TYPE homelab_service_supported gauge")
    lines.append("# HELP homelab_service_eol_seconds Seconds until current branch EOL; -1 unknown")
    lines.append("# TYPE homelab_service_eol_seconds gauge")
    lines.append("# HELP homelab_service_outdated_patch 1 if running != latest_patch")
    lines.append("# TYPE homelab_service_outdated_patch gauge")
    lines.append("# HELP homelab_service_outdated_major 1 if running branch != latest major branch")
    lines.append("# TYPE homelab_service_outdated_major gauge")
    lines.append("# HELP homelab_service_check_timestamp Unix epoch of last successful check")
    lines.append("# TYPE homelab_service_check_timestamp gauge")

    for service, instance, run_fn, ref_fn in SERVICES:
        try:
            running = strip_v(run_fn() or "")
        except Exception as e:
            print(f"[{service}] running probe failed: {e}", file=sys.stderr)
            running = None

        ref = {"latest_patch": None, "latest_major": None, "supported": -1, "eol_seconds": -1}
        if running:
            try:
                ref = ref_fn(running)
            except Exception as e:
                print(f"[{service}] reference lookup failed: {e}", file=sys.stderr)

        labels = {"service": service, "instance": instance}
        emit_info(lines, "homelab_service_running_version",
                  {**labels, "version": running or "unknown"})
        emit_info(lines, "homelab_service_latest_patch",
                  {**labels, "version": ref["latest_patch"] or "unknown"})
        emit_info(lines, "homelab_service_latest_major",
                  {**labels, "version": ref["latest_major"] or "unknown"})

        lines.append(f'homelab_service_supported{{service="{service}",instance="{instance}"}} {ref["supported"]}')
        lines.append(f'homelab_service_eol_seconds{{service="{service}",instance="{instance}"}} {ref["eol_seconds"]}')

        outdated_patch = 0
        if running and ref["latest_patch"] and parse_semver(running) and parse_semver(ref["latest_patch"]):
            outdated_patch = 1 if parse_semver(running) < parse_semver(ref["latest_patch"]) else 0
        outdated_major = 0
        if running and ref["latest_major"] and branch_of(running) and branch_of(ref["latest_major"]):
            outdated_major = 1 if branch_of(running) != branch_of(ref["latest_major"]) else 0
        lines.append(f'homelab_service_outdated_patch{{service="{service}",instance="{instance}"}} {outdated_patch}')
        lines.append(f'homelab_service_outdated_major{{service="{service}",instance="{instance}"}} {outdated_major}')

    lines.append(f"homelab_service_check_timestamp {int(time.time())}")
    lines.append("")  # trailing newline

    # Atomic write: write to tmp in same dir, fsync, rename.
    target_dir = os.path.dirname(OUTPUT_PATH)
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".homelab_versions.", dir=target_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUTPUT_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


if __name__ == "__main__":
    main()
