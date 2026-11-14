"""End-to-end encryption of biometric submissions to AMFA (amfa/common/e2e.py)."""
import json

import pytest

from amfa import config
from amfa.common import e2e
from amfa.common.crypto import DecryptionError, b64d, b64e

AAD = "sess_abc|FACE|ch_0123456789abcdef0123"
PLAIN = {"frames": ["data:image/jpeg;base64,AAAA", "data:image/jpeg;base64,BBBB"]}


def test_a_box_opens_for_the_use_it_was_made_for():
    box = e2e.encrypt(e2e.public_key(), PLAIN, AAD)
    assert set(box) == {"kid", "epk", "nonce", "ct"}
    assert e2e.decrypt(box, AAD) == PLAIN
    assert "AAAA" not in json.dumps(box)                               # the pictures are not in it


def test_a_box_is_new_every_time_even_for_the_same_content():
    a, b = e2e.encrypt(e2e.public_key(), PLAIN, AAD), e2e.encrypt(e2e.public_key(), PLAIN, AAD)
    assert a["ct"] != b["ct"] and a["epk"] != b["epk"] and a["nonce"] != b["nonce"]


@pytest.mark.parametrize("other", ["sess_abc|FACE|ch_ffffffffffffffffffff", "sess_zzz|FACE|ch_0123456789abcdef0123", "sess_abc|VOICE|ch_0123456789abcdef0123"])
def test_a_box_cannot_be_moved_to_another_session_challenge_or_factor(other):
    with pytest.raises(DecryptionError):
        e2e.decrypt(e2e.encrypt(e2e.public_key(), PLAIN, AAD), other)


def test_a_changed_byte_a_wrong_key_or_junk_is_refused():
    box = e2e.encrypt(e2e.public_key(), PLAIN, AAD)
    ct = bytearray(b64d(box["ct"])); ct[0] ^= 1
    with pytest.raises(DecryptionError):
        e2e.decrypt({**box, "ct": b64e(bytes(ct))}, AAD)
    with pytest.raises(DecryptionError):
        e2e.decrypt({**box, "kid": "v9.000000000000"}, AAD)
    with pytest.raises(DecryptionError):
        e2e.decrypt({**box, "epk": "AAAA"}, AAD)
    with pytest.raises(DecryptionError):
        e2e.decrypt({"kid": box["kid"]}, AAD)


def test_the_published_key_names_itself_and_can_be_pinned():
    k = e2e.public_key()
    assert k["alg"] == e2e.ALG and k["kid"].startswith(config.get().active_key_version + ".")
    assert len(k["fingerprint"]) == 64 and k == e2e.public_key()       # stable: a client can pin the fingerprint


def test_only_a_lone_e2e_key_counts_as_a_box():
    assert e2e.is_box({"e2e": {"kid": "x"}})
    assert not e2e.is_box({"frames": []}) and not e2e.is_box({"e2e": {}, "frames": []}) and not e2e.is_box({"e2e": "x"})


def test_known_answer_vector_shared_with_the_android_tests():
    """The same fixed vector is checked by E2ETest in mfa-android, so the two implementations agree with each other, not only
    each with itself: the same inputs give the same box here, and the box opens with the server's private key."""
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    v = json.loads((Path(__file__).parent / "e2e_vector.json").read_text())
    curve = ec.SECP256R1()
    server, eph = (ec.derive_private_key(int(v[k], 16), curve) for k in ("serverPrivate", "ephemeralPrivate"))
    key = e2e._derive(eph.exchange(ec.ECDH(), server.public_key()), b64d(v["nonce"]), v["kid"], v["aad"])
    assert b64e(AESGCM(key).encrypt(b64d(v["nonce"]), v["plaintextJson"].encode(), v["aad"].encode())) == v["ct"]
    assert AESGCM(key).decrypt(b64d(v["nonce"]), b64d(v["ct"]), v["aad"].encode()).decode() == v["plaintextJson"]
