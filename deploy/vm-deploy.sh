#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
C="docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.demo.yml -f deploy/docker-compose.vm.yml --env-file deploy/.env"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  say "installing Docker"
  curl -fsSL https://get.docker.com | sudo sh
fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
C="${C/docker/$DOCKER}"

IP=$(curl -fs -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip \
  || curl -fs https://api.ipify.org)
DOMAIN=${1:-${IP//./-}.sslip.io}

if [ ! -f deploy/.env ]; then
  say "generating deploy/.env for $DOMAIN"
  umask 077
  python3 - "$DOMAIN" > deploy/.env <<'EOF'
import base64, secrets, sys
d = sys.argv[1]
k = lambda: "v1:" + base64.b64encode(secrets.token_bytes(32)).decode()
t = lambda n=32: secrets.token_urlsafe(n)
print(f"""AMFA_DOMAIN={d}
BANK_DOMAIN=bank.{d}
AMFA_PUBLIC_URL=https://{d}
ACME_EMAIL=unused@invalid
AMFA_MASTER_KEYS={k()}
AMFA_FACTOR_MASTER_KEYS={k()}
AMFA_INTERNAL_TOKEN={t()}
AMFA_IP_SALT={t()}
DB_OWNER_PASSWORD={t(24)}
DB_APP_PASSWORD={t(24)}
DB_FACTOR_PASSWORD={t(24)}
REDIS_PASSWORD={t(24)}
AMFA_AGENT_BACKEND=local
AMFA_KEY_PROVIDER=env
AMFA_VOICE_PROMPT_CHECK=advisory
ADMIN_PASSWORD=Amfa-{t(15)}
BANK_SESSION_SECRET={secrets.token_hex(32)}""")
EOF
  umask 022
fi
set -a; . deploy/.env; set +a
[ -f deploy/demo-bank.env ] || install -m 600 /dev/null deploy/demo-bank.env

say "building images (first run: 10-20 minutes)"
$C build

say "starting the platform"
SERVICES="postgres-core postgres-factors redis redis-factors migrate face voice ueba core caddy"
$C up -d $SERVICES
for _ in $(seq 120); do
  $C exec -T core python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=3)" 2>/dev/null && break
  sleep 5
done

if [ ! -s deploy/demo-bank.env ]; then
  say "creating the platform admin, the Demo Bank tenant and its API key"
  $C exec -T -e AMFA_BOOTSTRAP_PASSWORD="$ADMIN_PASSWORD" core python -m amfa.cli init --admin-email admin@demo.local
  TID=$($C exec -T core python -m amfa.cli tenant create --name "Demo Bank" --slug demo-bank 2>/dev/null \
        | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
  KEY=$($C exec -T core python -m amfa.cli apikey create --tenant "$TID" --name demo-bank \
        --scopes users:write,users:read,transactions:write,transactions:read,proofs:verify,events:read,policy:read,policy:write,webhooks:write 2>/dev/null)
  umask 077
  python3 - "$TID" "$KEY" > deploy/demo-bank.env <<EOF
import json, sys
k = json.loads(sys.argv[2])
print(f"AMFA_BASE_URL=https://$AMFA_DOMAIN\nAMFA_TENANT_ID={sys.argv[1]}\nAMFA_TENANT_SLUG=demo-bank")
print(f"AMFA_KEY_ID={k['keyId']}\nAMFA_KEY_SECRET={k['secret']}\nBANK_SESSION_SECRET=$BANK_SESSION_SECRET")
EOF
  umask 022
fi

say "waiting for the TLS certificate"
for _ in $(seq 60); do curl -fs -o /dev/null "https://$AMFA_DOMAIN/healthz" && break; sleep 5; done

say "starting the demo bank and allowing it to embed the approval pages"
$C up -d demo-bank
sleep 5
$C exec -T demo-bank python - "https://bank.$AMFA_DOMAIN" <<'EOF'
import os, sys
sys.path.insert(0, "sdk/python")
from amfa_client import AmfaClient
c = AmfaClient(os.environ["AMFA_BASE_URL"], os.environ["AMFA_KEY_ID"], os.environ["AMFA_KEY_SECRET"])
cur = c.request("GET", "/v1/policy"); pol = cur["policy"]
if pol.get("allowedEmbedOrigins") != [sys.argv[1]]:
    pol["allowedEmbedOrigins"] = [sys.argv[1]]
    c.request("PUT", "/v1/policy", {"expectedVersion": cur["version"], "policy": pol})
print("embed origin:", sys.argv[1])
EOF

say "checks"
for u in "https://$AMFA_DOMAIN/readyz" "https://$AMFA_DOMAIN/admin" "https://bank.$AMFA_DOMAIN/"; do
  printf '  %s  %s\n' "$(curl -s -o /dev/null -m 20 -w '%{http_code}' "$u")" "$u"
done
$C ps --format 'table {{.Service}}\t{{.Status}}'
cat <<EOF

  Demo bank:      https://bank.$AMFA_DOMAIN
  Admin console:  https://$AMFA_DOMAIN/admin   (admin@demo.local / ADMIN_PASSWORD in deploy/.env)
EOF
