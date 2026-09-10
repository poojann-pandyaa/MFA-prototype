import numpy as np
from typing import List, Optional, Union

def compute_cosine_similarity(embedding1: np.ndarray, embedding2: np.ndarray) -> float:
    """
    Compute cosine similarity between two 1D or 2D embedding vectors.
    Returns float in range [-1.0, 1.0].
    """
    vec1 = np.asarray(embedding1).squeeze()
    vec2 = np.asarray(embedding2).squeeze()
    
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
        
    dot_product = np.dot(vec1, vec2)
    similarity = dot_product / (norm1 * norm2)
    return float(similarity)

def average_embeddings(embeddings: List[np.ndarray], normalize: bool = True) -> np.ndarray:
    """
    Combine/average multiple enrollment embeddings into a single reference voiceprint vector.
    Optionally re-normalizes to unit length (L2 norm = 1).
    """
    vecs = [np.asarray(e).squeeze() for e in embeddings]
    mean_vec = np.mean(vecs, axis=0)
    
    if normalize:
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
            
    return mean_vec


# ---------------------------------------------------------------------------
# 1:N Open-Set Identification Metrics
#
# Verification (1:1) is scored with FAR / FRR / EER. Identification (1:N) needs
# a different family of metrics, because the system must pick the right identity
# out of N candidates AND reject probes from speakers who are not enrolled.
# ---------------------------------------------------------------------------

def rank_n_accuracy(
    ranked_predictions: List[List[str]],
    true_ids: List[str],
    n: int = 1
) -> float:
    """
    Closed-set Rank-N identification accuracy.

    The fraction of probes whose true speaker_id appears within the top-N ranked
    candidates. Rank-1 accuracy is the headline closed-set identification number.

    Args:
        ranked_predictions: Per probe, the ranked candidate speaker_ids (best first).
        true_ids: Per probe, the ground-truth speaker_id.
        n: Rank cut-off.

    Returns:
        Accuracy in [0.0, 1.0]. Returns 0.0 for an empty probe set.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if len(ranked_predictions) != len(true_ids):
        raise ValueError(
            f"ranked_predictions ({len(ranked_predictions)}) and true_ids "
            f"({len(true_ids)}) must have equal length."
        )
    if not true_ids:
        return 0.0

    hits = sum(
        1 for ranked, truth in zip(ranked_predictions, true_ids)
        if truth in ranked[:n]
    )
    return hits / len(true_ids)


def cmc_curve(
    ranked_predictions: List[List[str]],
    true_ids: List[str],
    max_rank: Optional[int] = None
) -> List[float]:
    """
    Cumulative Match Characteristic curve.

    Element i holds Rank-(i+1) accuracy, so the curve is monotonically
    non-decreasing and cmc[0] is Rank-1 accuracy.

    Args:
        ranked_predictions: Per probe, the ranked candidate speaker_ids (best first).
        true_ids: Per probe, the ground-truth speaker_id.
        max_rank: Highest rank to evaluate. Defaults to the longest candidate list.

    Returns:
        List of accuracies of length max_rank.
    """
    if len(ranked_predictions) != len(true_ids):
        raise ValueError("ranked_predictions and true_ids must have equal length.")
    if not true_ids:
        return []

    if max_rank is None:
        max_rank = max((len(r) for r in ranked_predictions), default=0)
    if max_rank < 1:
        return []

    return [rank_n_accuracy(ranked_predictions, true_ids, n=r) for r in range(1, max_rank + 1)]


def open_set_identification_rates(
    results: List[dict],
    true_ids: List[Optional[str]]
) -> dict:
    """
    Open-set identification rates over a mixed probe set.

    Probes are split into two populations:
      - GENUINE probes, whose speaker IS enrolled (true_id is a speaker_id).
      - IMPOSTOR probes, whose speaker is NOT enrolled (true_id is None).

    Reported rates:
      - DIR (Detection & Identification Rate): fraction of genuine probes that are
        both accepted AND assigned the correct identity. This is the metric that
        matters most: accepting a genuine probe under the wrong identity is a
        failure, not a success.
      - FNIR (False Negative Identification Rate): 1 - DIR.
      - FPIR (False Positive Identification Rate): fraction of impostor probes
        wrongly accepted as some enrolled identity.
      - misidentification_rate: fraction of genuine probes accepted under the
        WRONG identity -- the failure mode unique to 1:N that has no 1:1 analogue.

    Args:
        results: Per probe, the dict returned by SpeakerVerifier.identify().
        true_ids: Per probe, ground-truth speaker_id, or None if not enrolled.

    Returns:
        Dict of rates and the underlying counts.
    """
    if len(results) != len(true_ids):
        raise ValueError("results and true_ids must have equal length.")

    genuine_total = 0
    genuine_correct = 0
    genuine_misidentified = 0
    impostor_total = 0
    impostor_accepted = 0

    for res, truth in zip(results, true_ids):
        accepted = bool(res.get("identified"))
        predicted = res.get("identified_speaker_id")

        if truth is None:
            impostor_total += 1
            if accepted:
                impostor_accepted += 1
        else:
            genuine_total += 1
            if accepted and predicted == truth:
                genuine_correct += 1
            elif accepted:
                genuine_misidentified += 1

    dir_rate = genuine_correct / genuine_total if genuine_total else 0.0
    return {
        "dir": dir_rate,
        "fnir": 1.0 - dir_rate,
        "fpir": impostor_accepted / impostor_total if impostor_total else 0.0,
        "misidentification_rate": (
            genuine_misidentified / genuine_total if genuine_total else 0.0
        ),
        "genuine_probes": genuine_total,
        "genuine_correctly_identified": genuine_correct,
        "genuine_misidentified": genuine_misidentified,
        "impostor_probes": impostor_total,
        "impostor_falsely_accepted": impostor_accepted,
    }


def effective_false_match_rate(far_1to1: float, gallery_size: int) -> float:
    """
    Approximate the per-attempt false-match rate of a 1:N search given a 1:1 FAR.

    A 1:1 comparison draws one score from the impostor distribution; a 1:N search
    draws N. Under the (optimistic) assumption that those comparisons are
    independent, the chance that at least one enrolled identity is falsely matched
    is 1 - (1 - FAR)**N.

    This is why an operating threshold tuned for 1:1 becomes progressively less
    safe as the gallery grows, and why identification_threshold is set stricter
    than the 1:1 threshold by default.

    NOTE: real speaker comparisons are correlated, so treat this as a first-order
    planning estimate, not a security guarantee.

    Args:
        far_1to1: Measured false accept rate of the 1:1 system, in [0.0, 1.0].
        gallery_size: Number of enrolled identities, N.

    Returns:
        Approximate per-attempt false match rate in [0.0, 1.0].
    """
    if not 0.0 <= far_1to1 <= 1.0:
        raise ValueError(f"far_1to1 must be in [0.0, 1.0], got {far_1to1}")
    if gallery_size < 0:
        raise ValueError(f"gallery_size must be non-negative, got {gallery_size}")
    return 1.0 - (1.0 - far_1to1) ** gallery_size
