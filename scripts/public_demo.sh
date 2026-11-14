#!/usr/bin/env bash
# Put the demo on the internet from this computer: two free Cloudflare quick tunnels (no account), one for AMFA and
# one for the demo bank, with the core and the bank restarted on those public addresses. The face, voice and
# behaviour services stay on localhost: only the core talks to them.
#
#   bash scripts/public_demo.sh          start, prints the public links
#   bash scripts/public_demo.sh stop
#
# Needs: cloudflared, the venv, the factor services already running (python -m amfa.cli serve face|ueba|voice),
# and the bank's tenant and API key in the environment:
#   AMFA_TENANT_ID  AMFA_TENANT_SLUG  AMFA_KEY_ID  AMFA_KEY_SECRET
# The links change every time it starts, and they work only while this computer is awake and online. For an address that
# stays, deploy to a server instead (deploy/vm-deploy.sh, docs/hosting.md).
#
# Codes: there is no email here, so AMFA hands each one-time code to the bank over a signed webhook and the bank shows it
# to the signed-in customer ("Show demo code"). Set BANK_DEMO_TOOLS=0 on anything that is not a demo.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
UVICORN=${UVICORN:-$(dirname "$PY")/uvicorn}
OUT=data/public
mkdir -p "$OUT"

stop() {
  pkill -f 'cloudflared tunnel --no-autoupdate' || true
  pkill -f 'amfa.cli serve core' || true
  pkill -f 'examples.demo_bank.bank:app' || true
}
if [ "${1:-}" = stop ]; then stop; echo stopped; exit 0; fi

: "${AMFA_TENANT_ID:?}" "${AMFA_TENANT_SLUG:?}" "${AMFA_KEY_ID:?}" "${AMFA_KEY_SECRET:?}"
command -v cloudflared >/dev/null || { echo "install cloudflared first (brew install cloudflared)"; exit 1; }
stop; sleep 2

# QUIC (UDP) is blocked on some networks; HTTP/2 over TCP works nearly everywhere.
for pair in core:8040 bank:8050; do
  name=${pair%%:*}; port=${pair##*:}
  : > "$OUT/tunnel_$name.log"
  nohup cloudflared tunnel --no-autoupdate --protocol http2 --url "http://127.0.0.1:$port" > "$OUT/tunnel_$name.log" 2>&1 &
done
for _ in $(seq 1 60); do
  CORE=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$OUT/tunnel_core.log" | head -1 || true)
  BANK=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$OUT/tunnel_bank.log" | head -1 || true)
  up=$(cat "$OUT"/tunnel_*.log | grep -c "Registered tunnel connection" || true)
  [ -n "$CORE" ] && [ -n "$BANK" ] && [ "$up" -ge 2 ] && break
  sleep 2
done
[ -n "${CORE:-}" ] && [ -n "${BANK:-}" ] || { echo "the tunnels did not come up; see $OUT/tunnel_*.log"; exit 1; }

# the bank may be framed by AMFA's pages only from its own public address
"$PY" - <<PYEOF
import os, sys
sys.path.insert(0, ".")
os.environ.setdefault("AMFA_ENV", "development")
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from amfa.core import db
with db.session() as s:
    t = s.execute(select(db.Tenant).where(db.Tenant.slug == os.environ["AMFA_TENANT_SLUG"])).scalars().one()
    p = dict(t.policy)
    p["allowedEmbedOrigins"] = (["$BANK"] + [o for o in p.get("allowedEmbedOrigins", []) if "trycloudflare.com" not in o])[:10]
    t.policy = p
    flag_modified(t, "policy")
PYEOF

SECRET=$(openssl rand -hex 24)
export AMFA_ENV=${AMFA_ENV:-development}
AMFA_AGENT_BACKEND=${AMFA_AGENT_BACKEND:-off} AMFA_PUBLIC_URL=$CORE AMFA_OTP_PROVIDER=webhook \
  AMFA_OTP_WEBHOOK_URL=http://127.0.0.1:8050/webhooks/otp AMFA_OTP_WEBHOOK_SECRET=$SECRET \
  nohup "$PY" -m amfa.cli serve core --host 127.0.0.1 --port 8040 > "$OUT/core.log" 2>&1 &
AMFA_BASE_URL=$CORE AMFA_OTP_WEBHOOK_SECRET=$SECRET BANK_SECURE_COOKIES=1 BANK_SESSION_SECRET=$(openssl rand -hex 32) \
  BANK_STATE_FILE=${BANK_STATE_FILE:-data/demo-bank-state.json} \
  nohup "$UVICORN" examples.demo_bank.bank:app --host 127.0.0.1 --port 8050 > "$OUT/bank.log" 2>&1 &
for _ in $(seq 1 40); do
  curl -fsS -o /dev/null "$CORE/healthz" && curl -fsS -o /dev/null "$BANK/" && break
  sleep 2
done
printf 'Demo bank (give people this): %s\nAMFA:                         %s\nApp: Settings > Bank URL = the bank address above\n' "$BANK" "$CORE" | tee "$OUT/LINKS.txt"
