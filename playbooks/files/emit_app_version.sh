#!/usr/bin/env bash
# Emit a homelab app's running version as a node_exporter textfile metric.
# Usage: emit_app_version.sh <service>
#
# Runs on the app's OWN host: for vaultwarden/n8n/docmost the app port is
# firewalled off from the monitoring host, so the version is read locally and
# published via node_exporter :9100. check_versions.py then reads
# homelab_app_running_version back from Prometheus and compares to GitHub.
set -euo pipefail

SERVICE="${1:?service name required}"
DIR=/var/lib/node_exporter/textfile
OUT="${DIR}/app_version_${SERVICE}.prom"

case "$SERVICE" in
  vaultwarden) raw="$(/opt/vaultwarden/bin/vaultwarden --version 2>/dev/null || true)" ;;
  n8n)         raw="$(n8n --version 2>/dev/null || true)" ;;
  docmost)     raw="$(grep -m1 '"version"' /opt/docmost/package.json 2>/dev/null || true)" ;;
  *)           echo "unknown service: $SERVICE" >&2; exit 1 ;;
esac

ver="$(printf '%s' "$raw" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
ver="${ver:-unknown}"

TMP="$(mktemp "${OUT}.XXXXXX")"
{
  echo "# HELP homelab_app_running_version Running version of a homelab app, published from its own host."
  echo "# TYPE homelab_app_running_version gauge"
  printf 'homelab_app_running_version{service="%s",version="%s"} 1\n' "$SERVICE" "$ver"
} > "$TMP"
chmod 644 "$TMP"
mv "$TMP" "$OUT"
