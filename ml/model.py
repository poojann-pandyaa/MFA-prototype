"""
PAD model: MobileNetV3-Small backbone (ImageNet-pretrained) + 3 heads.

- Do NOT train the backbone from scratch. ImageNet pretraining gives low-
  level edge/texture filters for free; training from random init on a
  PAD-sized dataset (tens of thousands, not millions, of images) loses
  meaningful accuracy for no benefit. See ml/README.md for why the face-
  recognition backbone (a much bigger training problem) isn't trained at
  all and uses frozen pretrained weights instead.
- Small on purpose: ~1.5-2M params, so the exported ONNX model (see
  export.py) is small enough for a browser tab and a mid-range phone.
"""
import torch
import torch.nn as nn
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights

from data.manifest import ATTACK_TYPES, FFT_MAP_SIZE


class PadModel(nn.Module):
    def __init__(self, pretrained: bool = True):
        super().__init__()
        weights = MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
        backbone = mobilenet_v3_small(weights=weights)

        # Keep the conv feature extractor, drop the ImageNet classifier.
        self.features = backbone.features
        self.avgpool = nn.AdaptiveAvgPool2d(1)
        feat_dim = backbone.classifier[0].in_features  # 576 for MobileNetV3-Small

        hidden = 128
        self.shared = nn.Sequential(
            nn.Linear(feat_dim, hidden),
            nn.Hardswish(inplace=True),
            nn.Dropout(0.2),
        )

        # Head 1: binary live/spoof (single logit, BCEWithLogitsLoss).
        self.binary_head = nn.Linear(hidden, 1)

        # Head 2: attack type (CrossEntropyLoss).
        self.attack_head = nn.Linear(hidden, len(ATTACK_TYPES))

        # Head 3: FFT magnitude auxiliary map, regressed from the last
        # conv feature map (before pooling) so it retains spatial layout.
        conv_channels = self._infer_conv_channels(backbone)
        self.fft_head = nn.Sequential(
            nn.Conv2d(conv_channels, 32, kernel_size=1),
            nn.Hardswish(inplace=True),
            nn.AdaptiveAvgPool2d((FFT_MAP_SIZE, FFT_MAP_SIZE)),
            nn.Conv2d(32, 1, kernel_size=1),
        )

    @staticmethod
    def _infer_conv_channels(backbone) -> int:
        # MobileNetV3-Small's last conv block output channels (576 before
        # the 1x1 "conv head" that classifier[0] normally consumes).
        last_conv = backbone.features[-1][0]
        return last_conv.out_channels

    def forward(self, x: torch.Tensor):
        conv_features = self.features(x)              # (B, C, H, W)
        pooled = self.avgpool(conv_features).flatten(1)  # (B, C)
        shared = self.shared(pooled)

        binary_logit = self.binary_head(shared).squeeze(-1)   # (B,)
        attack_logits = self.attack_head(shared)               # (B, num_attack_types)
        fft_pred = self.fft_head(conv_features).squeeze(1)     # (B, FFT_MAP_SIZE, FFT_MAP_SIZE)

        return binary_logit, attack_logits, fft_pred


class PadModelInferenceOnly(nn.Module):
    """
    Export-time wrapper exposing just the binary decision as a sigmoid
    probability, so the ONNX graph the server/web/Android clients load
    has a single, simple output rather than three raw logit tensors.
    """
    def __init__(self, pad_model: PadModel):
        super().__init__()
        self.pad_model = pad_model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        binary_logit, _, _ = self.pad_model(x)
        return torch.sigmoid(binary_logit)
