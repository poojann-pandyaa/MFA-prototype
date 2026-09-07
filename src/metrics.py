import numpy as np
from typing import List, Union

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
