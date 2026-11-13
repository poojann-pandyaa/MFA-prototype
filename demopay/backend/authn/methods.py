"""
The built authenticators: what each shows (`start`) and how its answer is judged (`check`).

start(...) -> (data the client may see, secret state the server keeps)
check(...) -> (outcome, detail)   outcome is "pass", "retry" (wrong, may try again) or "fail" (stop the payment)
"""
import datetime
import secrets

from app import config

# Questions with a "danger" answer. A danger answer means the payment looks like a scam in progress, so the step stops it.
QUESTION_BANK = [
    {"id": "q_call", "text": "Is someone on a call or chat telling you to make this payment?", "danger": "yes"},
    {"id": "q_secret", "text": "Were you told to keep this payment a secret?", "danger": "yes"},
    {"id": "q_arrest", "text": "Were you told you will be arrested or your account blocked if you do not pay?", "danger": "yes"},
    {"id": "q_prize", "text": "Were you promised a prize, refund or profit if you pay first?", "danger": "yes"},
]
HELPLINE = "Call 1930 (national cyber-crime helpline) or your bank before paying."


def _warning_text(family: str, channel: str) -> dict:
    if channel == "collect":
        return {"title": "This request will SEND money", "body": "You never enter a PIN to receive money. If you continue, money leaves your account."}
    if family == "COACHED":
        return {"title": "This looks like a common scam", "body": "Scammers often ask for payment during a call or chat. Banks and government agencies never do."}
    if family == "UNAUTHORISED":
        return {"title": "Unusual activity on your account", "body": "This payment looks different from how you normally use your account."}
    return {"title": "This payee looks risky", "body": "The receiving account has been reported before. Make sure you know and trust it."}


def start(method: str, *, payment, entry, family: str, hold_seconds: int, step_index: int):
    if method == "warning":
        qs = secrets.SystemRandom().sample(QUESTION_BANK, 2)
        data = {**_warning_text(family, payment.channel), "min_read_seconds": config.MIN_READ_SECONDS,
                "questions": [{"id": q["id"], "text": q["text"]} for q in qs], "helpline": HELPLINE}
        return data, {"danger": {q["id"]: q["danger"] for q in qs}}
    if method == "typed_confirm":
        name = (entry.registered_name if entry else None) or ""
        data = {"prompt": "Type the registered name of the person or business you are paying.",
                "payee_vpa": payment.payee_vpa, "name_known": bool(name), "length_hint": len(name) if name else None}
        return data, {"name": name}
    if method == "payee_check":
        data = {"payee_vpa": payment.payee_vpa, "registered_name": entry.registered_name if entry else None,
                "found": bool(entry), "similar_to": entry.similar_to if entry else None,
                "age_days": entry.age_days if entry else None, "reported": bool(entry and ((entry.fri or 0) >= 1 or (entry.complaints or 0) >= 1)),
                "question": "Is this who you mean to pay?"}
        return data, {}
    if method == "cooling_off":
        release = datetime.datetime.utcnow() + datetime.timedelta(seconds=hold_seconds)
        data = {"release_at": release.isoformat() + "Z", "hold_seconds": hold_seconds, "real_hold_seconds": config.REAL_HOLD_SECONDS,
                "shortened_for_demo": hold_seconds != config.REAL_HOLD_SECONDS,
                "tips": ["Stay on the line only if you started the call yourself.", "No agency or bank asks you to move money to a safe account.",
                         "Talk to someone you trust before the timer ends.", HELPLINE]}
        return data, {"release_at": release.timestamp()}
    raise ValueError(f"no such method: {method}")


def check(method: str, *, challenge, secret: dict, answer: dict):
    if method == "warning":
        waited = (datetime.datetime.utcnow() - challenge.created_at).total_seconds()
        if not answer.get("acknowledged"):
            return "retry", "Please confirm you have read the warning."
        if waited < config.MIN_READ_SECONDS:
            return "retry", "Please take a moment to read the warning first."
        answers = answer.get("answers") or {}
        if any(str(answers.get(qid, "")).lower() == danger for qid, danger in secret["danger"].items()):
            return "fail", "scam_indicators"
        if any(qid not in answers for qid in secret["danger"]):
            return "retry", "Please answer both questions."
        return "pass", "ok"
    if method == "typed_confirm":
        if not secret.get("name"):
            return "fail", "no_registered_name"
        typed = " ".join(str(answer.get("typed", "")).split()).lower()
        return ("pass", "ok") if typed == " ".join(secret["name"].split()).lower() else ("retry", "That does not match the registered name.")
    if method == "payee_check":
        return ("pass", "ok") if answer.get("confirmed") is True else ("fail", "user_declined_payee")
    if method == "cooling_off":
        if datetime.datetime.utcnow().timestamp() < secret["release_at"]:
            return "wait", "Still on hold."
        return "pass", "ok"
    raise ValueError(f"no such method: {method}")
