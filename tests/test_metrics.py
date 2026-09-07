import numpy as np
from src.metrics import compute_cosine_similarity, average_embeddings


def test_cosine_similarity_identical():
    v = np.array([0.2, -0.5, 0.8, 0.1])
    sim = compute_cosine_similarity(v, v)
    assert abs(sim - 1.0) < 1e-6


def test_cosine_similarity_opposite():
    v1 = np.array([1.0, 0.0, 0.0])
    v2 = np.array([-1.0, 0.0, 0.0])
    sim = compute_cosine_similarity(v1, v2)
    assert abs(sim - (-1.0)) < 1e-6


def test_cosine_similarity_orthogonal():
    v1 = np.array([1.0, 0.0, 0.0])
    v2 = np.array([0.0, 1.0, 0.0])
    sim = compute_cosine_similarity(v1, v2)
    assert abs(sim - 0.0) < 1e-6


def test_cosine_similarity_zero_vector():
    v1 = np.zeros(192)
    v2 = np.ones(192)
    sim = compute_cosine_similarity(v1, v2)
    assert sim == 0.0


def test_average_embeddings_normalized():
    e1 = np.array([1.0, 0.0, 0.0])
    e2 = np.array([0.0, 1.0, 0.0])
    avg = average_embeddings([e1, e2], normalize=True)
    norm = np.linalg.norm(avg)
    assert abs(norm - 1.0) < 1e-6
    # Component values should be equal
    assert abs(avg[0] - avg[1]) < 1e-6
