"""
EfficientAD 학습: AE 분기에만 색 증강 (brightness / contrast / saturation 중 하나).

논문: 선택된 항목에 대해 λ ~ Uniform(0.8, 1.2). 입력 [0,1], (B, 3, H, W).
"""

from __future__ import annotations

import random
from typing import Sequence, Tuple, Union

import torch

Range = Union[Tuple[float, float], Sequence[float]]


def _sample_factor(low: float, high: float, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    return torch.empty(1, device=device, dtype=dtype).uniform_(low, high)


def efficientad_ae_augment(
    x: torch.Tensor,
    brightness_range: Range = (0.8, 1.2),
    contrast_range: Range = (0.8, 1.2),
    saturation_range: Range = (0.8, 1.2),
) -> torch.Tensor:
    if x.dim() != 4 or x.shape[1] != 3:
        raise ValueError("x 는 (B, 3, H, W) 이어야 합니다.")
    b_lo, b_hi = float(brightness_range[0]), float(brightness_range[1])
    c_lo, c_hi = float(contrast_range[0]), float(contrast_range[1])
    s_lo, s_hi = float(saturation_range[0]), float(saturation_range[1])

    mode = random.choice(("brightness", "contrast", "saturation"))
    out = x.clone()
    dev, dt = x.device, x.dtype
    if mode == "brightness":
        f = _sample_factor(b_lo, b_hi, dev, dt)
        out = (out * f).clamp(0.0, 1.0)
    elif mode == "contrast":
        f = _sample_factor(c_lo, c_hi, dev, dt)
        mean = out.mean(dim=(2, 3), keepdim=True)
        out = ((out - mean) * f + mean).clamp(0.0, 1.0)
    else:
        f = _sample_factor(s_lo, s_hi, dev, dt)
        gray = out.mean(dim=1, keepdim=True).expand_as(out)
        out = (gray + (out - gray) * f).clamp(0.0, 1.0)
    return out
