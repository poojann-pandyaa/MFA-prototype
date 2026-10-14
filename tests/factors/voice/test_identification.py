"""Open-set 1:N decision, with synthetic embeddings: the integer part of a waveform's mean picks
the speaker direction, the fractional part adds small within-speaker variation, so genuine
similarity is high and impostor similarity near zero and thresholds can be exercised exactly."""
import numpy as np
import pytest

from amfa.factors.voice.gallery import SpeakerGallery, identify
from amfa.factors.voice.metrics import (average_embeddings, cmc_curve, effective_false_match_rate,
                                        open_set_identification_rates, rank_n_accuracy)

DIM = 192
THRESHOLD, MARGIN = 0.30, 0.05


def embed(code: int, utterance: int = 0) -> np.ndarray:
    value = code + utterance * 0.01
    variation = value - round(value)
    base = np.random.default_rng(round(value)).normal(size=DIM)
    base /= np.linalg.norm(base)
    if variation:
        jitter = np.random.default_rng(9000 + int(round(abs(variation) * 1000))).normal(size=DIM)
        base = base + variation * 2.0 * jitter / np.linalg.norm(jitter)
        base /= np.linalg.norm(base)
    return base


def enrol(*utterances: np.ndarray) -> np.ndarray:
    return average_embeddings(list(utterances))


def search(gallery, probe, **kw):
    return identify(gallery, probe, threshold=kw.pop("threshold", THRESHOLD),
                    margin=kw.pop("margin", MARGIN), **kw)


@pytest.fixture
def gallery():
    g = SpeakerGallery(embedding_dim=DIM)
    for code in range(1, 6):
        g.enroll_speaker(f"user_{code}", enrol(embed(code, 0), embed(code, 1)))
    return g


def twins():
    g = SpeakerGallery(embedding_dim=DIM)
    shared = enrol(embed(8, 0))
    g.enroll_speaker("user_twin_a", shared)
    g.enroll_speaker("user_twin_b", shared)
    return g


def test_identifies_the_correct_speaker(gallery):
    r = search(gallery, embed(3, 7))
    assert r["identified"] and r["identified_speaker_id"] == "user_3"
    assert r["decision"] == "IDENTIFIED" and r["rejection_reason"] is None
    assert r["gallery_size"] == 5 and r["top1_score"] >= r["threshold"]


def test_every_enrolled_speaker_is_found_at_rank_1(gallery):
    for code in range(1, 6):
        assert search(gallery, embed(code, 9))["identified_speaker_id"] == f"user_{code}"


def test_unenrolled_speaker_is_rejected_not_given_the_nearest_neighbour(gallery):
    r = search(gallery, embed(42))
    assert not r["identified"] and r["identified_speaker_id"] is None
    assert r["decision"] == "REJECTED_NO_MATCH"
    assert r["rejection_reason"] == "NO_ENROLLED_SPEAKER_ABOVE_THRESHOLD"
    assert r["candidates"] and r["top1_score"] < r["threshold"]


def test_ambiguous_probe_between_two_enrolments_is_rejected():
    r = search(twins(), embed(8, 3))
    assert not r["identified"] and r["decision"] == "REJECTED_AMBIGUOUS"
    assert r["rejection_reason"] == "INSUFFICIENT_MARGIN_BETWEEN_TOP_CANDIDATES"
    assert r["score_margin"] < r["margin"] and r["top1_score"] >= r["threshold"]


def test_top_k_cannot_weaken_the_decision():
    """The c706875 regression: top_k=1 once skipped the margin test and accepted an ambiguous probe."""
    g = twins()
    decisions = {}
    for k in (1, 2, 5):
        r = search(g, embed(8, 3), top_k=k)
        decisions[k] = r["decision"]
        assert len(r["candidates"]) == min(k, len(g))
        assert r["score_margin"] is not None
    assert set(decisions.values()) == {"REJECTED_AMBIGUOUS"}, decisions


def test_top_k_does_not_change_the_identified_speaker(gallery):
    base = search(gallery, embed(3, 7), top_k=5)
    for k in (1, 2, 3, 10):
        r = search(gallery, embed(3, 7), top_k=k)
        assert (r["identified_speaker_id"], r["decision"]) == (base["identified_speaker_id"], base["decision"])
        assert r["top1_score"] == pytest.approx(base["top1_score"])
        assert r["top2_score"] == pytest.approx(base["top2_score"])


def test_top_k_none_reports_everyone(gallery):
    assert len(search(gallery, embed(3, 7), top_k=None)["candidates"]) == 5


def test_empty_gallery_is_rejected():
    r = search(SpeakerGallery(embedding_dim=DIM), embed(1))
    assert not r["identified"] and r["decision"] == "REJECTED_EMPTY_GALLERY"
    assert r["rejection_reason"] == "NO_ENROLLED_IDENTITIES"
    assert r["candidates"] == [] and r["top1_score"] is None


def test_a_single_speaker_gallery_has_no_margin_condition():
    g = SpeakerGallery(embedding_dim=DIM)
    g.enroll_speaker("user_solo", enrol(embed(4, 0)))
    r = search(g, embed(4, 2))
    assert r["identified"] and r["top2_score"] is None and r["score_margin"] is None


def test_threshold_and_margin_are_honoured(gallery):
    strict = search(gallery, embed(2, 4), threshold=0.999)
    assert not strict["identified"] and strict["decision"] == "REJECTED_NO_MATCH"
    wide = search(gallery, embed(2, 4), margin=1.99)
    assert not wide["identified"] and wide["decision"] == "REJECTED_AMBIGUOUS"


def test_silent_probe_is_rejected(gallery):
    assert search(gallery, np.zeros(DIM))["decision"] == "REJECTED_NO_MATCH"


def test_rank_n_accuracy_and_cmc():
    ranked = [["a", "b", "c"], ["b", "a", "c"], ["c", "b", "a"]]
    truth = ["a", "a", "a"]
    assert rank_n_accuracy(ranked, truth, n=1) == pytest.approx(1 / 3)
    assert rank_n_accuracy(ranked, truth, n=2) == pytest.approx(2 / 3)
    curve = cmc_curve(ranked, truth)
    assert curve == sorted(curve) and curve[-1] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="equal length"):
        rank_n_accuracy([["a"]], ["a", "b"], n=1)


def test_open_set_rates_separate_misidentification_from_rejection():
    results = [{"identified": True, "identified_speaker_id": "a"},
               {"identified": True, "identified_speaker_id": "b"},
               {"identified": False, "identified_speaker_id": None},
               {"identified": False, "identified_speaker_id": None},
               {"identified": True, "identified_speaker_id": "a"}]
    rates = open_set_identification_rates(results, ["a", "a", "a", None, None])
    assert rates["genuine_probes"] == 3 and rates["impostor_probes"] == 2
    assert rates["dir"] == pytest.approx(1 / 3) and rates["fnir"] == pytest.approx(2 / 3)
    assert rates["fpir"] == pytest.approx(1 / 2)
    assert rates["misidentification_rate"] == pytest.approx(1 / 3)


def test_open_set_rates_on_a_real_gallery(gallery):
    probes = [embed(c, 8) for c in range(1, 6)] + [embed(99)]
    truth = [f"user_{c}" for c in range(1, 6)] + [None]
    rates = open_set_identification_rates([search(gallery, p) for p in probes], truth)
    assert rates["dir"] == pytest.approx(1.0) and rates["fpir"] == pytest.approx(0.0)
    assert rates["misidentification_rate"] == pytest.approx(0.0)


def test_false_match_rate_grows_with_the_gallery():
    assert effective_false_match_rate(0.01, 1) == pytest.approx(0.01)
    assert effective_false_match_rate(0.01, 25) == pytest.approx(0.2222, abs=1e-3)
    assert effective_false_match_rate(0.01, 100) > effective_false_match_rate(0.01, 25)
    with pytest.raises(ValueError):
        effective_false_match_rate(1.5, 10)
