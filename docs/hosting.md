# Hosting the demo so anyone can use it

## Right now: from this computer (free, temporary)

```bash
export PY=.venv/bin/python AMFA_TENANT_ID=... AMFA_TENANT_SLUG=demo-bank AMFA_KEY_ID=... AMFA_KEY_SECRET=...
bash scripts/public_demo.sh          # prints the public links; `bash scripts/public_demo.sh stop` closes them
```

It opens two Cloudflare quick tunnels (no account): one to AMFA, one to the demo bank. The core and bank restart on those
public addresses and listen on localhost only, so the tunnels are the only way in. Face, voice and behaviour services stay
on localhost.

* Give people the **bank** link. In the Android app they open Settings, enter it as the Bank URL and tap Test connection.
  The web demo works at the same link in any browser.
* The links change every time the script starts, and work only while this computer is awake and online.
* No email is sent. AMFA hands each one-time code to the bank over a signed webhook (`AMFA_OTP_PROVIDER=webhook`) and the bank
  shows it to the signed-in customer: web "Show demo code", Android "Show demo code". Turn that off with `BANK_DEMO_TOOLS=0`.
* The tunnels use HTTP/2 over TCP; QUIC (UDP) is blocked on some networks, which makes the default fail.

## A server that stays up

Needs a Linux machine with Docker (2+ vCPU, 8 GB RAM, port 443 open) and a domain pointing at it. The repository has the
pieces: `deploy/vm-deploy.sh` (secrets, Caddy HTTPS, Postgres, all services), `deploy/docker-compose.yml`,
`deploy/Caddyfile`. Then:

1. Replace the demo code delivery with a real one: `AMFA_OTP_PROVIDER=smtp` (see docs/one-time-codes.md) or a webhook to the
   bank's own mailer or SMS gateway, and set `BANK_DEMO_TOOLS=0`.
2. Run in production mode (`AMFA_ENV=production`): it refuses console code delivery, SQLite, a missing Redis and plain http.
3. Download the models on the server (`scripts/fetch_models.py`); train the risk model and decide about its fairness gate
   (docs/regression.md).
4. Put the server's https address in a new Android release (`bank_url` when running the workflow by hand).

## Before strangers use it: what to know

* **Faces and voices.** Anyone who sets up a face or voice check sends a biometric to this machine. Say so, keep a way to delete
  it (the bank already offers deletion), and do not leave a public demo up longer than needed.
* **Shared accounts.** The seeded customers (asha, rohan, ...) share the password `DemoPass#2026` and anyone can sign in as them.
* **No abuse protection beyond rate limits.** Sign-up is open; a quick tunnel has no firewall or bot filter.
* **Admin console.** `/admin` is reachable at the AMFA link; its password is random and in `data/`, not here. Do not share the AMFA link.
* **Demo tools.** With `BANK_DEMO_TOOLS=1` (the default) the bank shows one-time codes and offers staging buttons. A real bank must set it to 0.
