from amfa.core import orchestrator as orch

FACE = {"factor": "FACE", "status": "PENDING", "attempts": 1}


def guidance(reason, factor="FACE"):
    return orch._client_result({"failureReason": reason}, {**FACE, "factor": factor})["guidance"]


def test_every_face_check_failure_reads_the_same():
    reasons = ["HEAD_TURN_NOT_OBSERVED", "HEAD_TURN_NOT_CONTINUOUS", "NO_FRONTAL_FRAME",
               "NOT_ENOUGH_LIVE_FRAMES", "IDENTITY_CHANGED_DURING_TURN", "FACE_MISMATCH"]
    assert len({guidance(r) for r in reasons}) == 1
    assert guidance(reasons[0]) == orch.FACE_UNVERIFIED


def test_framing_problems_still_get_specific_help():
    assert "closer" in guidance("FACE_TOO_SMALL")
    assert guidance("NO_FACE_DETECTED") != orch.FACE_UNVERIFIED


def test_the_uniform_message_is_face_only():
    assert guidance("FACE_MISMATCH", factor="VOICE") is None


def test_behaviour_challenge_failures_tell_the_user_what_to_finish():
    for reason in ("CHALLENGE_INCOMPLETE", "CHALLENGE_MISSING"):
        text = guidance(reason, factor="UEBA")
        assert text and "line" in text and "phrase" in text


def test_a_reason_is_given_only_if_the_customers_own_numbers_support_it():
    """The model names its strongest contributions even when they are tiny; 'this payee was added recently' about a
    payee paid for two years would be false."""
    from amfa.engine.context import Plan, TxnType
    from amfa.engine.rationale import customer_notice
    reasons = [("payee_age_days", 0.02), ("place_seen", 0.01), ("payee_seen", 0.01)]
    plan = Plan(threatLevel="T1", riskReasons=reasons)
    old_friend = {"payee_age_days": 700, "place_seen": 40, "payee_seen": 30}
    assert customer_notice(plan, TxnType.TRANSFER, old_friend)["reason"] == "This is a routine check to keep your account safe."
    stranger = {"payee_age_days": None, "place_seen": 0, "payee_seen": 0}
    text = customer_notice(plan, TxnType.TRANSFER, stranger)["reason"]
    assert "you are in an unfamiliar place" in text and "this payee has not been paid before" in text
    assert "added recently" in customer_notice(plan, TxnType.TRANSFER, {"payee_age_days": 5, "payee_seen": 3, "place_seen": 9})["reason"]
    assert customer_notice(Plan(threatLevel=None, riskReasons=[]), TxnType.TRANSFER, {})["reason"].startswith("This is a routine")
