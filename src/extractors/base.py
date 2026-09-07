from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Tuple
import numpy as np

class BaseSpeakerExtractor(ABC):
    """Abstract Base Class for all speaker verification model extractors."""
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name identifier for the speaker embedding model."""
        pass

    @abstractmethod
    def extract_embedding(self, audio_path: Path) -> Tuple[np.ndarray, float]:
        """
        Extract speaker embedding for a given audio file.
        
        Returns:
            Tuple[np.ndarray, float]: (embedding_vector, inference_time_sec)
        """
        pass
