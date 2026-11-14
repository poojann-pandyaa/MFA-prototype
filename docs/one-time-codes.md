# One-time codes: email, text, or an authenticator app

Each bank (tenant) decides how its customers get the second factor. The setting is `otpMethods` in the tenant policy
(admin console, policy editor, or `PUT /v1/policy`):

```json
{ "otpMethods": ["email", "authenticator"] }
```

| Method | What the customer does | What the bank must provide |
|---|---|---|
| `email` | Receives the code at the address the bank gave when creating the user | `contact: {"channel": "email", "to": "…"}` on the user, and an SMTP provider (below) |
| `sms` | Receives the code at the number the bank gave | `contact: {"channel": "sms", "to": "+91…"}` and the `webhook` provider (your SMS gateway) |
| `authenticator` | Sets up Google/Microsoft Authenticator, Authy or any TOTP app (QR code, `otpauth://` link, or typed key) and enters the code it shows | nothing; the secret is made and sealed by AMFA |

The default allows all three. What a method left out means: it is neither offered nor accepted. A customer whose only
route is a method the bank dropped has no code factor, and the engine plans other checks (or a review) instead.
An authenticator wins over a sent code when both are allowed and the customer has set one up. A code from an app
works once (the next one appears after 30 seconds).

## Delivery providers (`AMFA_OTP_PROVIDER`)

* `console` (default, development only): the code is printed in the core's log as `[amfa dev OTP -> email] …`.
* `smtp`: real email. Set `AMFA_SMTP_HOST`, `AMFA_SMTP_PORT` (587), `AMFA_SMTP_USER`, `AMFA_SMTP_PASSWORD`,
  `AMFA_SMTP_FROM`. Production always uses STARTTLS; in development a local mail catcher without TLS also works.
* `webhook`: AMFA posts a signed message (`AMFA_OTP_WEBHOOK_URL`, `AMFA_OTP_WEBHOOK_SECRET`, 32+ characters) to the
  bank's own gateway, for text messages or a bank-branded mailer.

Codes last 3 minutes, a session sends at most 3, and a resend needs a short wait.
