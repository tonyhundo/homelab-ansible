# Read-only access to kv-v2 homelab/* secrets used by Ansible playbooks.
path "kv/data/homelab/*" {
  capabilities = ["read"]
}

path "kv/metadata/homelab/*" {
  capabilities = ["read", "list"]
}

path "kv/metadata/homelab" {
  capabilities = ["list"]
}
