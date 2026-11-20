# amfa-client

The bank's side of Adaptive MFA in one file: it signs every request, creates the customer in AMFA, asks for an
approval of a payment (or any sensitive action), and verifies the signed proof before the bank moves any money.

## Install

From a GitHub Release (download the `.whl` from the release's assets):

```bash
pip install amfa_client-1.0.0-py3-none-any.whl
```

Or straight from the repository, no release needed:

```bash
pip install "amfa-client @ git+https://github.com/poojann-pandyaa/MFA-prototype.git#subdirectory=sdk/python"
```

Add `[offline-proof]` (`pip install "amfa-client[offline-proof] ..."`) only if the bank checks proofs itself with
`verify_proof_offline`; it pulls in PyJWT and `cryptography`.

## Use

```python
from amfa_client import AmfaClient, AmfaError

amfa = AmfaClient("https://amfa.example.com", key_id="ak_...", secret="...")   # the tenant API key from the admin console

user = amfa.create_user("customer-123")["id"]                     # once per customer
link = amfa.enrollment_link(user, factors=["FACE"])["url"]        # face setup, opened on AMFA's own page

txn = amfa.initiate(user_id=user, external_id="pay-1", type="TRANSFER", amount_minor=250_000, currency="INR",
                    payee_ref="payee-9", device_id="web-abc123", ip="203.0.113.5")
txn["plan"]["required"]        # the checks AMFA chose, e.g. ["OTP", "UEBA"]
txn["challengeUrl"]            # where the customer does them

# after the customer finishes, and only then, move the money:
token = amfa.fetch_proof(txn["transactionId"])["token"]
amfa.verify_proof(token, user_ref="customer-123", external_txn="pay-1", amount_minor=250_000,
                  currency="INR", payee_ref="payee-9", type="TRANSFER", consume=True)   # raises AmfaError if it is not valid
```

`consume=True` uses the proof up, so the same approval can never be replayed; a proof bound to one amount and payee is
refused for any other. Webhooks from AMFA are checked with `verify_webhook(secret, body, timestamp, signature)`.

The full contract is AMFA's own API; see the repository's `README.md` and `INTEGRATION.md`.
