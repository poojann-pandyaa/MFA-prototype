"""
RawNet2 Anti-Spoofing Implementation.

Based on: https://github.com/NTU-ROSE/RawNet2
Original Author: Hemlata Tak (tak@eurecom.fr), EURECOM, 2021
Reference: Tak et al., "End-to-End anti-spoofing with RawNet2", ICASSP 2021
"""

from .model import RawNet
from .inference import RawNet2Detector

__all__ = ["RawNet", "RawNet2Detector"]
