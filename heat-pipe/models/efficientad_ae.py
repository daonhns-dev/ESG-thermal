"""
EfficientAD AutoEncoder — 논문 Table 8 (EncConv + 비대칭 DecConv).

공식 EfficientAD(models.py)의 EncConv / DecConv 와 동일: bilinear 목표 크기
3→8→15→32→63→127→64, 디코더 Dropout 0.2, 최종 (B, 384, 64, 64) 특징 맵.
(글로벌 분기 L_STAE 는 Student 후반 384채널과 이 출력을 맞춤; RGB 재구성이 아님.)
"""

from __future__ import annotations

import torch.nn as nn
import torch.nn.functional as F

from .efficientad_norm import imagenet_normalize


class EncConv(nn.Module):
    def __init__(self, in_channels: int = 3) -> None:
        super().__init__()
        self.enconv1 = nn.Conv2d(in_channels, 32, kernel_size=4, stride=2, padding=1)
        self.enconv2 = nn.Conv2d(32, 32, kernel_size=4, stride=2, padding=1)
        self.enconv3 = nn.Conv2d(32, 64, kernel_size=4, stride=2, padding=1)
        self.enconv4 = nn.Conv2d(64, 64, kernel_size=4, stride=2, padding=1)
        self.enconv5 = nn.Conv2d(64, 64, kernel_size=4, stride=2, padding=1)
        self.enconv6 = nn.Conv2d(64, 64, kernel_size=8, stride=1, padding=0)

    def forward(self, x):
        x = F.relu(self.enconv1(x))
        x = F.relu(self.enconv2(x))
        x = F.relu(self.enconv3(x))
        x = F.relu(self.enconv4(x))
        x = F.relu(self.enconv5(x))
        x = self.enconv6(x)
        return x


class DecConv(nn.Module):
    """is_bn=False 시 공식과 같이 nn.Dropout(p=0.2) × 6."""

    def __init__(self, is_bn: bool = False, out_channels: int = 384) -> None:
        super().__init__()
        self.is_bn = is_bn
        self.deconv1 = nn.Conv2d(64, 64, kernel_size=4, stride=1, padding=2)
        self.deconv2 = nn.Conv2d(64, 64, kernel_size=4, stride=1, padding=2)
        self.deconv3 = nn.Conv2d(64, 64, kernel_size=4, stride=1, padding=2)
        self.deconv4 = nn.Conv2d(64, 64, kernel_size=4, stride=1, padding=2)
        self.deconv5 = nn.Conv2d(64, 64, kernel_size=4, stride=1, padding=2)
        self.deconv6 = nn.Conv2d(64, 64, kernel_size=4, stride=1, padding=2)
        self.deconv7 = nn.Conv2d(64, 64, kernel_size=3, stride=1, padding=1)
        self.deconv8 = nn.Conv2d(64, out_channels, kernel_size=3, stride=1, padding=1)
        if self.is_bn:
            self.reg1 = nn.BatchNorm2d(64)
            self.reg2 = nn.BatchNorm2d(64)
            self.reg3 = nn.BatchNorm2d(64)
            self.reg4 = nn.BatchNorm2d(64)
            self.reg5 = nn.BatchNorm2d(64)
            self.reg6 = nn.BatchNorm2d(64)
        else:
            p = 0.2
            self.reg1 = nn.Dropout(p=p)
            self.reg2 = nn.Dropout(p=p)
            self.reg3 = nn.Dropout(p=p)
            self.reg4 = nn.Dropout(p=p)
            self.reg5 = nn.Dropout(p=p)
            self.reg6 = nn.Dropout(p=p)

    def forward(self, x):
        x = F.interpolate(x, size=3, mode="bilinear", align_corners=False)
        x = F.relu(self.deconv1(x))
        x = self.reg1(x)
        x = F.interpolate(x, size=8, mode="bilinear", align_corners=False)
        x = F.relu(self.deconv2(x))
        x = self.reg2(x)
        x = F.interpolate(x, size=15, mode="bilinear", align_corners=False)
        x = F.relu(self.deconv3(x))
        x = self.reg3(x)
        x = F.interpolate(x, size=32, mode="bilinear", align_corners=False)
        x = F.relu(self.deconv4(x))
        x = self.reg4(x)
        x = F.interpolate(x, size=63, mode="bilinear", align_corners=False)
        x = F.relu(self.deconv5(x))
        x = self.reg5(x)
        x = F.interpolate(x, size=127, mode="bilinear", align_corners=False)
        x = F.relu(self.deconv6(x))
        x = self.reg6(x)
        x = F.interpolate(x, size=64, mode="bilinear", align_corners=False)
        x = F.relu(self.deconv7(x))
        x = self.deconv8(x)
        return x


class EfficientADAutoEncoder(nn.Module):
    """
    입력 x: (B, in_channels, H, W), [0, 1]. 내부에서 ImageNet 정규화 후 인코더/디코더.
    반환: (B, ae_feature_channels, 64, 64) — Teacher/Student(후반)와 동일 해상도·채널 스케일.

    Args:
        in_channels: 입력 채널 수 (기본 3, RGB).
        is_bn: True면 디코더에 BatchNorm2d, False면 Dropout(0.2) 적용.
               논문 권장값은 is_bn=False.
        ae_feature_channels: 디코더 출력 채널 수 (기본 384).
    """

    def __init__(self, in_channels: int = 3, is_bn: bool = False, ae_feature_channels: int = 384):
        super().__init__()
        self.encoder = EncConv(in_channels=in_channels)
        self.decoder = DecConv(is_bn=is_bn, out_channels=ae_feature_channels)

    def forward(self, x):
        xn = imagenet_normalize(x)
        z = self.encoder(xn)
        return self.decoder(z)
