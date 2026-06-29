# Vault runbook

Operational procedures for `vault.example.com` (HashiCorp Vault, raft storage, TLS via Step-CA, manual unseal).

All commands below run from **emperor** unless noted. Set these once per shell:

```bash
export VAULT_ADDR=https://vault.example.com:8200
export VAULT_CACERT=/usr/local/share/ca-certificates/example-ca.crt
```

If `vault` CLI is not installed on emperor, SSH to the vault host and run the commands there (`ssh admin@vault.example.com`).

---

## 1. First-time init (ONCE, after `terraform apply`)

```bash
vault operator init -key-shares=5 -key-threshold=3
```

Capture the output immediately. It prints:

- 5 unseal keys (base64)
- 1 initial root token

**Store all six values in Vaultwarden as six separate entries** (Unseal Key 1..5 and Root Token). Do not commit them to the repo. Losing them is unrecoverable — the Vault data is permanently sealed.

Unseal (supply any 3 of the 5 keys):

```bash
vault operator unseal <key-1>
vault operator unseal <key-2>
vault operator unseal <key-3>
```

`vault status` should report `Sealed: false`.

Log in with the root token:

```bash
vault login <root-token>
```

## 2. Bootstrap KV engine, policy, AppRole

From `~/homelab/ansible/`:

```bash
ansible-playbook -i hosts --limit vault playbooks/vault_bootstrap.yml
```

This playbook:

- enables `kv-v2` at the `kv/` path
- writes the `ansible` policy from `files/vault/policies/ansible.hcl`
- enables the `approle` auth method
- creates the `ansible` role
- fetches role_id + secret_id and writes them to `/etc/vault-ansible/env` on emperor (mode 0600, owner admin)

You'll need `VAULT_TOKEN` set to the root token while running it:

```bash
export VAULT_TOKEN=<root-token>
```

After it completes, unset the root token:

```bash
unset VAULT_TOKEN
```

## 3. Seed secrets (ONCE, current plaintext values)

```bash
ansible-playbook -i hosts playbooks/seed_secrets.yml
```

(uses `hosts: localhost` — runs against the controller, writes via the Vault API.)

Then verify:

```bash
vault kv list kv/homelab
```

After this succeeds:

1. Delete `ansible/playbooks/vars_local.yml` (formerly `vault.yml`).
2. Remove any remaining plaintext values from `mariadb.yml`, `stepca.yml`, etc.
3. **Rotate** every secret — they are in git history. Rotation workflow per secret:
   - Change the value on the real system (e.g. `ALTER USER 'gitea'@'...' IDENTIFIED BY '...'` for DB creds).
   - `vault kv put kv/homelab/<name> <field>=<new-value>`.
   - Re-run the service playbook to propagate.

## 4. Daily operation — running playbooks

Every `ansible-playbook` invocation needs the AppRole env vars:

```bash
source /etc/vault-ansible/env
ansible-playbook -i hosts playbooks/<name>.yml
```

Or persistently add to `~/.bashrc`:

```bash
[ -r /etc/vault-ansible/env ] && . /etc/vault-ansible/env
```

`terraform/main.tf`'s provisioners also source this file — see the `local-exec` blocks.

## 5. Post-reboot unseal

Vault seals itself on every restart. After a reboot of the LXC:

```bash
ssh admin@vault.example.com
sudo systemctl status vault            # confirm it's running
vault status                           # Sealed=true expected
vault operator unseal <key-1>
vault operator unseal <key-2>
vault operator unseal <key-3>
```

You can also run the unseal loop from emperor with `VAULT_ADDR` set — Vault accepts unseal operations over the network listener.

## 6. Health checks

```bash
# Reachable + TLS valid
curl -s --cacert $VAULT_CACERT $VAULT_ADDR/v1/sys/health | jq

# From prometheus
curl -s 'http://prometheus.example.com:9090/api/v1/query?query=up{job="vault"}'
```

## 7. Backup

The raft data lives at `/opt/vault/data` on the LXC. Use Vault's own snapshot API:

```bash
vault operator raft snapshot save vault-$(date +%Y%m%d).snap
```

Store snapshots alongside other Proxmox backups in `~/homelab/backups/`.

## 8. Emergency: lost unseal keys

No recovery. Destroy the LXC, `terraform apply` to rebuild, re-init, re-seed. You will lose all stored secrets — they must be regenerated or re-entered from Vaultwarden.

## 9. Future work

- **Auto-unseal** via a second transit-Vault LXC (documented as a TODO in MEMORY.md).
- **Audit log** — enable `vault audit enable file file_path=/var/log/vault_audit.log` once the LXC has more disk.
- **Periodic token renewal** — AppRole secret_ids expire by default (currently disabled via `secret_id_ttl=0`). Revisit if policy tightens.
