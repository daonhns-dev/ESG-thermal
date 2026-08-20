"""
EfficientAD 공통: ImageNet 정규화 (torchvision 사전학습과 동일 스케일).
입력 x는 ToTensor 기준 [0, 1], shape (B, 3, H, W).
"""

from __future__ import annotations

import torch

# ImageNet 3채널 기준
_IMAGENET_MEAN_3 = [0.485, 0.456, 0.406]
_IMAGENET_STD_3 = [0.229, 0.224, 0.225]
 
# 1채널용: 3채널 평균값 (공식 구현에서 grayscale→3ch 복제 후 정규화와 수학적 동치)
_IMAGENET_MEAN_1 = [sum(_IMAGENET_MEAN_3) / 3.0]
_IMAGENET_STD_1 = [sum(_IMAGENET_STD_3) / 3.0]

def imagenet_normalize(x: torch.Tensor) -> torch.Tensor:
    """
    ImageNet 스케일 정규화.
    x: (B, C, H, W), [0, 1] 범위.
    """
    C = x.shape[1]
    if C == 3:
        mean = x.new_tensor(_IMAGENET_MEAN_3).view(1, 3, 1, 1)
        std = x.new_tensor(_IMAGENET_STD_3).view(1, 3, 1, 1)
    elif C == 1:
        mean = x.new_tensor(_IMAGENET_MEAN_1).view(1, 1, 1, 1)
        std = x.new_tensor(_IMAGENET_STD_1).view(1, 1, 1, 1)
    else:
        # 4ch 등 - 범용 폴백
        mean = x.new_tensor([0.5] * C).view(1, C, 1, 1)
        std = x.new_tensor([0.25] * C).view(1, C, 1, 1)
    return (x - mean) / (std + 1e-11)
