#!/usr/bin/env bash
# Import a service page into Docmost via markdown upload.
# Invoked by the n8n post-deploy fanout workflow (Execute Command node).
#
# Required env: SERVICE, SERVICE_FQDN, IP, PORT, DESCRIPTION
# Credentials:  DOCMOST_EMAIL, DOCMOST_PASSWORD
#               (sourced from /etc/default/docmost-import if present)
# Optional env: DOCMOST_URL       (default http://docmost.example.com:3000)
#               DOCMOST_SPACE_ID  (default 019c8048-de11-75ba-b126-e0d2c7965d22)
#
# Emits the import response JSON on stdout. Exits non-zero on any failure.

set -euo pipefail

if [ -f /etc/default/docmost-import ]; then
  # shellcheck disable=SC1091
  . /etc/default/docmost-import
fi

: "${SERVICE:?SERVICE is required}"
: "${SERVICE_FQDN:?SERVICE_FQDN is required}"
: "${IP:?IP is required}"
: "${PORT:?PORT is required}"
: "${DESCRIPTION:=}"
: "${DOCMOST_EMAIL:?DOCMOST_EMAIL is required}"
: "${DOCMOST_PASSWORD:?DOCMOST_PASSWORD is required}"
: "${DOCMOST_URL:=http://docmost.example.com:3000}"
: "${DOCMOST_SPACE_ID:=019c8048-de11-75ba-b126-e0d2c7965d22}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

cookies="$tmp/cookies.txt"
md="$tmp/${SERVICE}.md"
login_body="$tmp/login.json"

jq -n --arg email "$DOCMOST_EMAIL" --arg pass "$DOCMOST_PASSWORD" \
  '{email:$email,password:$pass}' >"$login_body"

cat >"$md" <<EOF
# ${SERVICE}

**Host:** ${SERVICE_FQDN}
**IP:** ${IP}
**Port:** ${PORT}
**Deployed:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Description

${DESCRIPTION}

## Source

- Terraform: [terraform.tfvars](https://gitea.example.com/admin/terraform/src/branch/main/terraform.tfvars)
- Ansible playbook: [${SERVICE}.yml](https://gitea.example.com/admin/ansible/src/branch/main/playbooks/${SERVICE}.yml)
EOF

login_resp=$(curl -sk -c "$cookies" -X POST "${DOCMOST_URL}/api/auth/login" \
  -H 'Content-Type: application/json' \
  --data-binary "@${login_body}")

if ! printf '%s' "$login_resp" | jq -e '.success == true' >/dev/null 2>&1; then
  printf 'docmost login failed: %s\n' "$login_resp" >&2
  exit 1
fi

import_resp=$(curl -sk -b "$cookies" -X POST "${DOCMOST_URL}/api/pages/import" \
  -F "spaceId=${DOCMOST_SPACE_ID}" \
  -F "file=@${md}")

if ! printf '%s' "$import_resp" | jq -e '.success == true' >/dev/null 2>&1; then
  printf 'docmost import failed: %s\n' "$import_resp" >&2
  exit 1
fi

printf '%s\n' "$import_resp"
