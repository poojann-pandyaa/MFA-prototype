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
