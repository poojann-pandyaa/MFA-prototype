"""Feature extraction, templates and scoring. No service involved."""
import math

import numpy as np
import pytest

from data.behaviour import (mouse_profile, mouse_sample, session_profile,
                            session_sample, shape_profile, shape_sample,
                            typing_profile, typing_sample)
from features import keystroke as ks, mouse as ms, session as se, shape as sh
from features import transactional as tx
from scoring.combine import (DimensionCalibrator, attenuate, band, blend,
                             combine, confidence, coverage_status)


@pytest.fixture
def rng():
    return np.random.default_rng(11)


def typed(text, t0=0.0, gap=150.0, hold=90.0):
    out, t = [], t0
    for ch in text:
        out.append({"key": ch, "down": t, "up": t + hold})
        t += gap
    return out, t


# ------------------------------------------------------------ keystroke

def test_enrollment_counts():
    """6 recorded, 1 discarded, 5 used."""
    assert (ks.TARGET_SAMPLES, ks.DISCARD_FIRST, ks.MIN_SAMPLES) == (6, 1, 5)


def test_template_uses_five_of_six(rng):
    p = typing_profile(rng)
    t = ks.build_template([ks.extract(typing_sample(p, rng)) for _ in range(6)])
    assert t["n_samples"] == 5


def test_discard_skipped_when_it_would_leave_too_few(rng):
    p = typing_profile(rng)
    t = ks.build_template([ks.extract(typing_sample(p, rng)) for _ in range(5)])
    assert t is not None and t["n_samples"] == 5


def test_template_refused_below_minimum(rng):
    p = typing_profile(rng)
    assert ks.build_template([ks.extract(typing_sample(p, rng)) for _ in range(4)]) is None


def test_template_stores_variance(rng):
    p = typing_profile(rng)
    t = ks.build_template([ks.extract(typing_sample(p, rng)) for _ in range(6)])
    assert all(s is not None and s > 0 for s in t["dwell_std"])


def test_genuine_closer_than_impostor(rng):
    a, b = typing_profile(rng), typing_profile(rng)
    t = ks.build_template([ks.extract(typing_sample(a, rng)) for _ in range(6)])
    g = np.mean([ks.distance(t, ks.extract(typing_sample(a, rng))) for _ in range(10)])
    i = np.mean([ks.distance(t, ks.extract(typing_sample(b, rng))) for _ in range(10)])
    assert i > g * 1.4


def test_corrected_typo_aligns():
    """
    'teh', two backspaces, then the rest. Replay must leave positions intact;
    merely dropping backspaces shifted every later key by two.
    """
    phrase = ks.ENROLL_PHRASE
    first, t = typed("teh")
    fix = [{"key": "Backspace", "down": t, "up": t + 60},
           {"key": "Backspace", "down": t + 150, "up": t + 210}]
    rest, _ = typed(phrase[1:], t0=t + 300)
    s = ks.extract(first + fix + rest)
    assert s["coverage"] == 1.0
    assert s["summary"]["backspaces"] == 2


def test_uncorrected_typo_lowers_coverage():
    s = ks.extract(typed("tha quick brown fox 2026")[0])
    assert 0.9 < s["coverage"] < 1.0


def test_empty_attempt_is_scoreable():
    s = ks.extract([])
    assert s["coverage"] == 0.0 and np.isnan(s["dwell"]).all()


def test_half_phrase_coverage():
    s = ks.extract(typed(ks.ENROLL_PHRASE[:12])[0])
    assert s["coverage"] == pytest.approx(0.5)


def test_zero_variance_position_does_not_explode():
    ev, _ = typed(ks.ENROLL_PHRASE)
    t = ks.build_template([ks.extract(ev) for _ in range(6)])
    d = ks.distance(t, ks.extract(ev))
    assert d is not None and math.isfinite(d)


def test_template_has_no_nan_for_json(rng):
    import json
    p = typing_profile(rng)
    t = ks.build_template([ks.extract(typing_sample(p, rng)) for _ in range(6)])
    assert "NaN" not in json.dumps(t)


# ---------------------------------------------------------------- shape

def test_trapezoid_slopes_are_60_degrees_on_a_2_to_1_pad():
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = sh.SHAPE
    rise = math.degrees(math.atan2(y0 - y1, (x1 - x0) * sh.ASPECT))
    fall = math.degrees(math.atan2(y3 - y2, (x3 - x2) * sh.ASPECT))
    assert rise == pytest.approx(60, abs=0.5)
    assert fall == pytest.approx(60, abs=0.5)
    assert y1 == y2                       # flat top


def test_shape_is_open():
    (x0, y0), (x3, y3) = sh.SHAPE[0], sh.SHAPE[-1]
    assert math.hypot((x3 - x0) * sh.ASPECT, y3 - y0) > 1.0


def test_shape_enrollment_count():
    assert sh.MIN_SAMPLES == 4


def test_full_trace_coverage(rng):
    assert sh.extract(shape_sample(shape_profile(rng), rng))["coverage"] == 1.0


def test_partial_trace_coverage(rng):
    pts = shape_sample(shape_profile(rng), rng)
    cov = sh.extract(pts[: len(pts) // 2])["coverage"]
    assert 0.3 < cov < 0.7


def test_empty_trace_is_scoreable():
    s = sh.extract([])
    assert s["coverage"] == 0.0


def test_incomplete_traces_excluded_from_template(rng):
    p = shape_profile(rng)
    full = [sh.extract(shape_sample(p, rng)) for _ in range(3)]
    pts = shape_sample(p, rng)
    half = sh.extract(pts[: len(pts) // 2])
    assert sh.build_template(full + [half]) is None      # only 3 complete


def test_shape_genuine_closer_than_impostor(rng):
    a, b = shape_profile(rng), shape_profile(rng)
    t = sh.build_template([sh.extract(shape_sample(a, rng)) for _ in range(4)])
    g = np.mean([sh.distance(t, sh.extract(shape_sample(a, rng))) for _ in range(10)])
    i = np.mean([sh.distance(t, sh.extract(shape_sample(b, rng))) for _ in range(10)])
    assert i > g


def test_stale_template_is_not_compared():
    """A template from the old zigzag has no split statistics."""
    assert sh.distance({"time_mean": [1.0]}, sh.extract([])) is None


# ---------------------------------------------------------------- blend

def test_blend_endpoints():
    assert blend(1.0, 3.0, 1.0) == 1.0          # complete: unchanged
    assert blend(None, 3.0, 0.0) == 3.0         # nothing: typical impostor
    assert blend(1.0, 3.0, 0.5) == pytest.approx(2.0)


def test_coverage_status():
    assert coverage_status(1.0) == "OK"
    assert coverage_status(0.5) == "PARTIAL"
    assert coverage_status(0.0) == "MISSING"


# ---------------------------------------------------------------- mouse

def test_mouse_needs_enough_points(rng):
    pts, cl = mouse_sample(mouse_profile(rng), rng, n_moves=1)
    assert ms.extract(pts[:5], cl) is None


def test_mouse_separates_users(rng):
    a, b = mouse_profile(rng), mouse_profile(rng)
    def s(p):
        pts, cl = mouse_sample(p, rng)
        return ms.extract(pts, cl)
    t = ms.build_template([s(a) for _ in range(6)])
    assert np.mean([ms.distance(t, s(b)) for _ in range(8)]) > \
           np.mean([ms.distance(t, s(a)) for _ in range(8)])


# -------------------------------------------------------------- session

def test_session_is_not_enrollable(rng):
    p = session_profile(rng)
    assert se.build_profile([se.extract(session_sample(p, rng))]) is None


def test_session_zero_ratio_does_not_explode(rng):
    p = session_profile(rng)
    prof = se.build_profile([se.extract(session_sample(p, rng)) for _ in range(10)])
    assert se.distance(prof, se.extract(session_sample(p, rng))) < se.MAX_DEVIATION


# -------------------------------------------------- calibration, fusion

def test_calibration_orders_probabilities():
    cal = DimensionCalibrator.fit(list(np.random.normal(1, 0.3, 60)),
                                  list(np.random.normal(4, 0.8, 60)))
    assert cal.probability(1.0) < cal.probability(4.0)


def test_calibration_rejects_too_few_samples():
    with pytest.raises(ValueError, match="at least 5"):
        DimensionCalibrator.fit([1.0, 1.1], [4.0, 4.1])


def test_missing_dimensions_skipped_in_fusion():
    risk, used = combine({"keystroke": 0.9, "session": None})
    assert used == ["keystroke"] and risk == pytest.approx(0.9, abs=0.01)


def test_no_dimensions_is_neutral():
    assert combine({"a": None}) == (0.5, [])


def test_damping_reduces_compounding():
    u, _ = combine({k: 0.9 for k in "abcd"}, damping=0.0)
    d, _ = combine({k: 0.9 for k in "abcd"})
    assert 0.9 < d < u


def test_bands():
    assert (band(0.1), band(0.5), band(0.9)) == ("LOW", "MEDIUM", "HIGH")


def test_confidence_and_attenuation():
    assert confidence(10, 40) == 0.25 and confidence(80, 40) == 1.0
    assert attenuate(0.9, 0.25) == pytest.approx(0.6)


# --------------------------------------------------------- transactional

def test_transactional_features_causal():
    from data.synth import generate
    f = tx.compute_batch(generate(n_users=15, n_days=30, seed=2))
    first = f.groupby("user_id").head(1)
    assert (first.amount_zscore == 0).all() and (first.is_new_device == 0).all()


def test_circular_hour_wraparound():
    night = np.array([23, 0, 1, 23, 0])
    assert tx._circular_hour_deviation(0, night) < 1.0
    assert tx._circular_hour_deviation(12, night) > 4.0
