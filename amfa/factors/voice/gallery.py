from __future__ import annotations

from typing import Any, Optional

import numpy as np

DEFAULT_EMBEDDING_DIM = 192


class SpeakerGallery:
    """Enrolled voiceprints held as unit vectors, so a search over N speakers is one
    matrix-vector product. The gallery only ranks; accept or reject is `identify`'s job."""

    def __init__(self, embedding_dim: int = DEFAULT_EMBEDDING_DIM):
        if embedding_dim <= 0:
            raise ValueError(f"embedding_dim must be positive, got {embedding_dim}")
        self.embedding_dim = int(embedding_dim)
        self._voiceprints: dict[str, np.ndarray] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._matrix: Optional[np.ndarray] = None
        self._ordered_ids: Optional[list[str]] = None

    def enroll_speaker(self, speaker_id: str, voiceprint: np.ndarray,
                       metadata: Optional[dict[str, Any]] = None, overwrite: bool = False) -> dict:
        if not isinstance(speaker_id, str) or not speaker_id:
            raise ValueError("speaker_id must be a non-empty string.")
        if speaker_id in self._voiceprints and not overwrite:
            raise ValueError(f"speaker_id '{speaker_id}' is already enrolled. "
                             "Pass overwrite=True to replace the existing voiceprint.")
        vec = np.asarray(voiceprint, dtype=np.float64).squeeze()
        if vec.ndim != 1:
            raise ValueError(f"voiceprint must be a 1-D vector, got shape {np.asarray(voiceprint).shape}.")
        if vec.shape[0] != self.embedding_dim:
            raise ValueError(f"voiceprint dimension mismatch: gallery expects {self.embedding_dim}, "
                             f"got {vec.shape[0]}.")
        if not np.all(np.isfinite(vec)):
            raise ValueError("voiceprint contains NaN or infinite values.")
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            raise ValueError("voiceprint has zero magnitude and cannot be enrolled.")
        self._voiceprints[speaker_id] = vec / norm
        self._metadata[speaker_id] = dict(metadata or {})
        self._matrix = self._ordered_ids = None
        return {"speaker_id": speaker_id, "gallery_size": len(self._voiceprints),
                "embedding_dim": self.embedding_dim}

    def remove_speaker(self, speaker_id: str) -> bool:
        if speaker_id not in self._voiceprints:
            return False
        del self._voiceprints[speaker_id]
        del self._metadata[speaker_id]
        self._matrix = self._ordered_ids = None
        return True

    def get_voiceprint(self, speaker_id: str) -> np.ndarray:
        if speaker_id not in self._voiceprints:
            raise KeyError(f"speaker_id '{speaker_id}' is not enrolled.")
        return self._voiceprints[speaker_id].copy()

    def get_metadata(self, speaker_id: str) -> dict[str, Any]:
        if speaker_id not in self._voiceprints:
            raise KeyError(f"speaker_id '{speaker_id}' is not enrolled.")
        return dict(self._metadata[speaker_id])

    @property
    def speaker_ids(self) -> list[str]:
        return list(self._voiceprints)

    def __len__(self) -> int:
        return len(self._voiceprints)

    def __contains__(self, speaker_id: object) -> bool:
        return speaker_id in self._voiceprints

    @property
    def matrix(self) -> np.ndarray:
        if self._matrix is None:
            self._ordered_ids = list(self._voiceprints)
            self._matrix = (np.vstack([self._voiceprints[s] for s in self._ordered_ids])
                            if self._ordered_ids
                            else np.empty((0, self.embedding_dim), dtype=np.float64))
        return self._matrix

    def search(self, probe_embedding: np.ndarray, top_k: Optional[int] = 5) -> list[dict]:
        """Rank every enrolled voiceprint by cosine similarity to the probe. No threshold is
        applied, so an unenrolled speaker still gets a nearest neighbour."""
        if top_k is not None and top_k <= 0:
            raise ValueError(f"top_k must be positive or None, got {top_k}")
        if not self._voiceprints:
            return []
        probe = np.asarray(probe_embedding, dtype=np.float64).squeeze()
        if probe.ndim != 1:
            raise ValueError("probe_embedding must be a 1-D vector, got shape "
                             f"{np.asarray(probe_embedding).shape}.")
        if probe.shape[0] != self.embedding_dim:
            raise ValueError(f"probe dimension mismatch: gallery expects {self.embedding_dim}, "
                             f"got {probe.shape[0]}.")
        matrix = self.matrix
        norm = float(np.linalg.norm(probe))
        # a silent or degenerate probe scores 0 against everyone, so any positive threshold rejects it
        scores = np.zeros(matrix.shape[0]) if norm == 0.0 else matrix @ (probe / norm)
        order = np.argsort(-scores, kind="stable")
        if top_k is not None:
            order = order[:top_k]
        ids = self._ordered_ids or []
        return [{"speaker_id": ids[i], "cosine_similarity": float(scores[i]), "rank": pos + 1}
                for pos, i in enumerate(order)]


def identify(gallery: SpeakerGallery, probe_embedding: np.ndarray, *, threshold: float,
             margin: float, top_k: int = 5) -> dict:
    """1:N open-set identification. Two independent conditions must both hold:

    1. top-1 similarity >= threshold, so a probe from someone not enrolled is not mapped to
       whichever enrolled user happens to be closest;
    2. top-1 minus top-2 >= margin, so a probe sitting between two enrolments is not
       confidently given to the first.

    `top_k` only limits the candidates reported. The top-2 score the margin test needs is always
    retrieved, so top_k=1 cannot switch the ambiguity check off."""
    search_k = None if top_k is None else max(int(top_k), 2)
    scored = gallery.search(probe_embedding, top_k=search_k)
    base = {"gallery_size": len(gallery), "threshold": float(threshold), "margin": float(margin),
            "candidates": scored if top_k is None else scored[:top_k]}
    if not scored:
        return {**base, "identified": False, "identified_speaker_id": None,
                "decision": "REJECTED_EMPTY_GALLERY", "rejection_reason": "NO_ENROLLED_IDENTITIES",
                "top1_score": None, "top2_score": None, "score_margin": None}

    top1 = float(scored[0]["cosine_similarity"])
    top2 = float(scored[1]["cosine_similarity"]) if len(scored) > 1 else None
    gap = None if top2 is None else top1 - top2
    speaker = None
    if top1 < threshold:
        decision, reason = "REJECTED_NO_MATCH", "NO_ENROLLED_SPEAKER_ABOVE_THRESHOLD"
    elif gap is not None and gap < margin:
        decision, reason = "REJECTED_AMBIGUOUS", "INSUFFICIENT_MARGIN_BETWEEN_TOP_CANDIDATES"
    else:
        decision, reason, speaker = "IDENTIFIED", None, str(scored[0]["speaker_id"])
    return {**base, "identified": speaker is not None, "identified_speaker_id": speaker,
            "decision": decision, "rejection_reason": reason,
            "top1_score": top1, "top2_score": top2, "score_margin": gap}
