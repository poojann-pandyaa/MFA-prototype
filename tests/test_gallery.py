import numpy as np
import pytest

from src.gallery import SpeakerGallery


def make_voiceprint(dim: int = 192, seed: int = 0) -> np.ndarray:
    """Deterministic unit-norm vector for gallery testing."""
    rng = np.random.default_rng(seed)
    vec = rng.normal(size=dim)
    return vec / np.linalg.norm(vec)


def test_enroll_and_lookup():
    gallery = SpeakerGallery()
    result = gallery.enroll_speaker("user_A", make_voiceprint(seed=1))

    assert result["gallery_size"] == 1
    assert result["embedding_dim"] == 192
    assert "user_A" in gallery
    assert len(gallery) == 1
    assert gallery.speaker_ids == ["user_A"]


def test_voiceprints_are_stored_normalized():
    gallery = SpeakerGallery()
    unnormalized = make_voiceprint(seed=2) * 17.0
    gallery.enroll_speaker("user_A", unnormalized)

    stored = gallery.get_voiceprint("user_A")
    assert abs(np.linalg.norm(stored) - 1.0) < 1e-9


def test_duplicate_enrollment_requires_overwrite():
    gallery = SpeakerGallery()
    gallery.enroll_speaker("user_A", make_voiceprint(seed=3))

    with pytest.raises(ValueError, match="already enrolled"):
        gallery.enroll_speaker("user_A", make_voiceprint(seed=4))

    gallery.enroll_speaker("user_A", make_voiceprint(seed=4), overwrite=True)
    assert len(gallery) == 1


def test_dimension_mismatch_is_rejected():
    gallery = SpeakerGallery(embedding_dim=192)
    with pytest.raises(ValueError, match="dimension mismatch"):
        gallery.enroll_speaker("user_A", np.ones(128))


def test_zero_and_nonfinite_voiceprints_are_rejected():
    gallery = SpeakerGallery()
    with pytest.raises(ValueError, match="zero magnitude"):
        gallery.enroll_speaker("user_A", np.zeros(192))

    bad = make_voiceprint(seed=5)
    bad[0] = np.nan
    with pytest.raises(ValueError, match="NaN or infinite"):
        gallery.enroll_speaker("user_B", bad)


def test_remove_speaker_invalidates_search():
    gallery = SpeakerGallery()
    vp_a = make_voiceprint(seed=6)
    gallery.enroll_speaker("user_A", vp_a)
    gallery.enroll_speaker("user_B", make_voiceprint(seed=7))

    # Force the search matrix to be built before mutating
    assert len(gallery.search(vp_a, top_k=2)) == 2

    assert gallery.remove_speaker("user_B") is True
    assert gallery.remove_speaker("user_B") is False

    candidates = gallery.search(vp_a, top_k=5)
    assert len(candidates) == 1
    assert candidates[0]["speaker_id"] == "user_A"


def test_search_ranks_by_descending_similarity():
    gallery = SpeakerGallery()
    for i in range(5):
        gallery.enroll_speaker(f"user_{i}", make_voiceprint(seed=100 + i))

    probe = gallery.get_voiceprint("user_3")
    candidates = gallery.search(probe, top_k=5)

    assert candidates[0]["speaker_id"] == "user_3"
    assert candidates[0]["cosine_similarity"] > 0.99
    assert [c["rank"] for c in candidates] == [1, 2, 3, 4, 5]

    scores = [c["cosine_similarity"] for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_search_respects_top_k():
    gallery = SpeakerGallery()
    for i in range(10):
        gallery.enroll_speaker(f"user_{i}", make_voiceprint(seed=200 + i))

    assert len(gallery.search(make_voiceprint(seed=999), top_k=3)) == 3
    assert len(gallery.search(make_voiceprint(seed=999), top_k=None)) == 10


def test_search_on_empty_gallery_returns_no_candidates():
    gallery = SpeakerGallery()
    assert gallery.search(make_voiceprint(seed=8)) == []


def test_search_matches_manual_cosine_similarity():
    from src.metrics import compute_cosine_similarity

    gallery = SpeakerGallery()
    vectors = {f"user_{i}": make_voiceprint(seed=300 + i) for i in range(4)}
    for sid, vec in vectors.items():
        gallery.enroll_speaker(sid, vec)

    probe = make_voiceprint(seed=777)
    candidates = {c["speaker_id"]: c["cosine_similarity"] for c in gallery.search(probe, top_k=None)}

    for sid, vec in vectors.items():
        assert abs(candidates[sid] - compute_cosine_similarity(vec, probe)) < 1e-9
