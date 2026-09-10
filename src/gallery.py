"""
Speaker Gallery for 1:N Open-Set Speaker Identification.

This module provides the enrolled-identity store required to move the Voice
Biometric factor from 1:1 verification ("is this claimed user U?") to 1:N
open-set identification ("which of the N enrolled users is this, if any?").

DESIGN NOTES
------------
1. All voiceprints are stored L2-normalized. Cosine similarity against a
   normalized probe therefore reduces to a single matrix-vector product,
   so a search over N speakers costs one BLAS call rather than N Python-level
   cosine computations.

2. The gallery is OPEN-SET. A probe from a speaker who is not enrolled must be
   rejected, not silently mapped to the nearest neighbour. Rejection logic lives
   in SpeakerVerifier.identify(); the gallery itself only returns ranked
   candidates and never makes an accept/reject decision.

3. The stacked search matrix is rebuilt lazily and invalidated on every
   mutation, so enrollment and removal stay O(1) at call time.
"""

from typing import Any, Dict, List, Optional, Union

import numpy as np

DEFAULT_EMBEDDING_DIM = 192


class SpeakerGallery:
    """
    An in-memory store of enrolled speaker voiceprints supporting ranked 1:N search.

    In the Adaptive MFA architecture this is the component the Authentication
    Orchestrator populates from the User Profile Store before invoking the
    Voice factor.
    """

    def __init__(self, embedding_dim: int = DEFAULT_EMBEDDING_DIM):
        """
        Initialize an empty speaker gallery.

        Args:
            embedding_dim: Expected dimensionality of enrolled voiceprints
                           (192 for ECAPA-TDNN / spkrec-ecapa-voxceleb).
        """
        if embedding_dim <= 0:
            raise ValueError(f"embedding_dim must be positive, got {embedding_dim}")

        self.embedding_dim = int(embedding_dim)
        self._voiceprints: Dict[str, np.ndarray] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}

        # Lazily rebuilt search structures
        self._matrix: Optional[np.ndarray] = None
        self._ordered_ids: Optional[List[str]] = None

    # ------------------------------------------------------------------
    # Enrollment management
    # ------------------------------------------------------------------
    def enroll_speaker(
        self,
        speaker_id: str,
        voiceprint: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Add an enrolled identity to the gallery.

        Args:
            speaker_id: Unique identifier for the enrolled user.
            voiceprint: Reference embedding, typically SpeakerVerifier.enroll()['voiceprint'].
            metadata: Optional non-biometric attributes (e.g. enrollment sample count).
            overwrite: If False, re-enrolling an existing speaker_id raises.

        Returns:
            Dict with speaker_id, gallery_size and embedding_dim.
        """
        if not isinstance(speaker_id, str) or not speaker_id:
            raise ValueError("speaker_id must be a non-empty string.")

        if speaker_id in self._voiceprints and not overwrite:
            raise ValueError(
                f"speaker_id '{speaker_id}' is already enrolled. "
                "Pass overwrite=True to replace the existing voiceprint."
            )

        vec = np.asarray(voiceprint, dtype=np.float64).squeeze()

        if vec.ndim != 1:
            raise ValueError(
                f"voiceprint must be a 1-D vector, got shape {np.asarray(voiceprint).shape}."
            )
        if vec.shape[0] != self.embedding_dim:
            raise ValueError(
                f"voiceprint dimension mismatch: gallery expects {self.embedding_dim}, "
                f"got {vec.shape[0]}."
            )
        if not np.all(np.isfinite(vec)):
            raise ValueError("voiceprint contains NaN or infinite values.")

        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            raise ValueError("voiceprint has zero magnitude and cannot be enrolled.")

        self._voiceprints[speaker_id] = vec / norm
        self._metadata[speaker_id] = dict(metadata or {})
        self._invalidate()

        return {
            "speaker_id": speaker_id,
            "gallery_size": len(self._voiceprints),
            "embedding_dim": self.embedding_dim,
        }

    def remove_speaker(self, speaker_id: str) -> bool:
        """
        Remove an enrolled identity. Returns True if a record was removed.
        """
        if speaker_id not in self._voiceprints:
            return False
        del self._voiceprints[speaker_id]
        del self._metadata[speaker_id]
        self._invalidate()
        return True

    def get_voiceprint(self, speaker_id: str) -> np.ndarray:
        """
        Return the stored (unit-normalized) voiceprint for an enrolled speaker.
        """
        if speaker_id not in self._voiceprints:
            raise KeyError(f"speaker_id '{speaker_id}' is not enrolled.")
        return self._voiceprints[speaker_id].copy()

    def get_metadata(self, speaker_id: str) -> Dict[str, Any]:
        """Return stored non-biometric metadata for an enrolled speaker."""
        if speaker_id not in self._voiceprints:
            raise KeyError(f"speaker_id '{speaker_id}' is not enrolled.")
        return dict(self._metadata[speaker_id])

    @property
    def speaker_ids(self) -> List[str]:
        """Enrolled speaker identifiers, in insertion order."""
        return list(self._voiceprints.keys())

    def __len__(self) -> int:
        return len(self._voiceprints)

    def __contains__(self, speaker_id: object) -> bool:
        return speaker_id in self._voiceprints

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def _invalidate(self) -> None:
        self._matrix = None
        self._ordered_ids = None

    def _rebuild(self) -> None:
        """Rebuild the stacked (N, D) search matrix from the current enrollments."""
        self._ordered_ids = list(self._voiceprints.keys())
        if self._ordered_ids:
            self._matrix = np.vstack([self._voiceprints[s] for s in self._ordered_ids])
        else:
            self._matrix = np.empty((0, self.embedding_dim), dtype=np.float64)

    @property
    def matrix(self) -> np.ndarray:
        """The stacked (N, D) matrix of unit-normalized enrolled voiceprints."""
        if self._matrix is None:
            self._rebuild()
        return self._matrix

    def search(
        self,
        probe_embedding: np.ndarray,
        top_k: Optional[int] = 5
    ) -> List[Dict[str, Union[str, float, int]]]:
        """
        Score a probe embedding against every enrolled voiceprint and rank the results.

        This performs the raw 1:N comparison only. It applies no threshold and makes
        no accept/reject decision -- an empty gallery yields an empty candidate list,
        and an unenrolled speaker still yields a nearest neighbour. Open-set rejection
        is applied by SpeakerVerifier.identify().

        Args:
            probe_embedding: Test-utterance embedding (need not be pre-normalized).
            top_k: Number of ranked candidates to return. None returns all N.

        Returns:
            List of {speaker_id, cosine_similarity, rank}, sorted by descending
            similarity, with rank starting at 1.
        """
        if top_k is not None and top_k <= 0:
            raise ValueError(f"top_k must be positive or None, got {top_k}")

        if len(self._voiceprints) == 0:
            return []

        probe = np.asarray(probe_embedding, dtype=np.float64).squeeze()
        if probe.ndim != 1:
            raise ValueError(
                f"probe_embedding must be a 1-D vector, got shape "
                f"{np.asarray(probe_embedding).shape}."
            )
        if probe.shape[0] != self.embedding_dim:
            raise ValueError(
                f"probe dimension mismatch: gallery expects {self.embedding_dim}, "
                f"got {probe.shape[0]}."
            )

        gallery_matrix = self.matrix
        probe_norm = float(np.linalg.norm(probe))
        if probe_norm == 0.0:
            # A silent/degenerate probe scores 0.0 against every identity and will
            # therefore be rejected downstream by any positive threshold.
            scores = np.zeros(gallery_matrix.shape[0], dtype=np.float64)
        else:
            # Gallery rows are unit-normalized, so this dot product is cosine similarity.
            scores = gallery_matrix @ (probe / probe_norm)

        order = np.argsort(-scores, kind="stable")
        if top_k is not None:
            order = order[:top_k]

        ids = self._ordered_ids or []
        return [
            {
                "speaker_id": ids[idx],
                "cosine_similarity": float(scores[idx]),
                "rank": position + 1,
            }
            for position, idx in enumerate(order)
        ]
