"""
Anti-Spoofing / Presentation Attack Detection (PAD) Module.

This package provides presentation attack detection for Voice MFA,
distinguishing BONAFIDE human speech from SPOOF presentations (TTS / Voice Conversion).
"""

from .rawnet2.inference import RawNet2Detector

__all__ = ["RawNet2Detector"]
