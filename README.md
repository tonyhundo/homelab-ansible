# Homelab Infrastructure-as-Code — Ansible

Declarative configuration management for a self-hosted [Proxmox](https://www.proxmox.com/) cluster:
base OS hardening, secrets management, full-stack observability, an internal PKI, and one-command
service deployments. Every play is idempotent and FQCN-clean.

> **Note on sanitization.** This is a public mirror of a private operations repo. The real domain and
> addresses have been replaced with documentation placeholders — `example.com`, `192.0.2.0/24` and
> `198.51.100.0/24` (RFC 5737), and the admin user generalized to `admin`. **No secrets have ever been
> committed:** every credential is fetched at runtime from HashiCorp Vault (see [Secrets](#secrets-vault)).

---

## Architecture

Provisioning and configuration are split across two repositories:

| Layer | Tool | Responsibility |
| --- | --- | --- |
| **Provision** | Terraform *(companion repo)* | Clone VMs / create LXC containers on Proxmox, assign IPs, inject SSH keys |
| **Configure** | **Ansible** *(this repo)* | Harden the OS, manage users, deploy services, wire up monitoring & TLS |

```
Terraform apply ─▶ Proxmox VM/LXC ─▶ cloud-init/SSH ready
                                       │
                                       ▼
        Ansible ── base/all.yml ──▶ packages · users · SSH hardening · node_exporter · TLS cert
                │
                ├── network/  ──▶ Pi-hole DNS registration
                ├── secrets/  ──▶ HashiCorp Vault (AppRole, kv-v2)
                ├── monitoring/─▶ Prometheus · Grafana · exporters · version-compliance
                └── services/ ──▶ Gitea · MariaDB · n8n · Vaultwarden · Docmost · …
```

## Highlights

- **Secretless repo.** All credentials live in HashiCorp Vault and are pulled lazily via the
  `community.hashi_vault` lookup with **AppRole** auth — the repo contains only Vault *paths*, never values.
- **Internal PKI.** A [Step-CA](https://smallstep.com/docs/step-ca/) instance issues short-lived TLS
  certificates; every host fetches the CA root and its own cert into `/etc/ssl/` at provision time.
- **Full observability.** Prometheus scrapes `node_exporter` on every host plus service exporters
  (Gitea, MariaDB, Pi-hole, SNMP, Jellyfin); Grafana dashboards are provisioned **as code** with stable
  UIDs so re-runs adopt rather than duplicate.
- **Version-compliance monitoring.** A custom checker (`files/check_versions.py`) compares each service's
  running version against endoflife.date / GitHub releases and exposes `homelab_service_*` metrics that
  drive a "Lifecycle" compliance table in Grafana. Apps unreachable from the monitoring host publish their
  own version via a small `node_exporter` textfile emitter.
- **Config-drift detection.** A scheduled play reconciles the fleet against `main` and reports drift by email.
- **Debian 13 (trixie) native.** Uses `deb822_repository` for APT sources — no `apt-key`/`software-properties-common`.

## Repository layout

```
playbooks/
├── base/         # all.yml, container_base.yml, node_exporter, step_ca_client — run on every host
├── monitoring/   # prometheus, grafana, *_exporter, otel_collector, version_checker, app_version_emitter
├── services/     # gitea, n8n, mariadb, vaultwarden, docmost, rundeck, gamefeed, unifi
├── network/      # pihole, pihole_dns, pihole_doh, stepca
├── secrets/      # hashicorp_vault, vault_bootstrap, RUNBOOK_vault.md
├── maintenance/  # patch, proxmox_backup, ansible_drift
├── group_vars/   # all.yml — every secret as a Vault lookup
├── files/ · templates/
hosts             # static inventory (INI)
ansible.cfg
requirements.yml  # community.hashi_vault, community.docker, ansible.posix
```

Each category subfolder carries symlinks back to the shared `group_vars/`, `files/`, and `templates/`
so playbooks resolve secrets and file lookups correctly whether run from the static inventory or an
inline one (`-i '<ip>,'`) during first-boot provisioning.

## How it's used

```bash
# install collections
ansible-galaxy collection install -r requirements.yml

# load the Vault AppRole env (written by vault_bootstrap.yml), then run against the fleet
source /etc/vault-ansible/env
ansible-playbook -i hosts playbooks/base/all.yml

# configure a single newly-provisioned host over its IP
ansible-playbook -i '192.0.2.100,' --user admin \
  playbooks/base/all.yml

# a single service against its host group
ansible-playbook -i hosts --limit prometheus playbooks/monitoring/prometheus.yml
```

## Tech stack

Ansible · Proxmox VE · Debian 13 · HashiCorp Vault · Step-CA · Prometheus · Grafana ·
node_exporter & friends · Pi-hole · Gitea · systemd · Docker (select services)

---

*Companion Terraform repo provisions the underlying VMs/containers. Secrets, TLS material, and real
network details are intentionally excluded from this public mirror.*
