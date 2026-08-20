"""
Patch Description Network — 논문 Table 6 (PDN-S), Table 7 (PDN-M).

구현은 공식 저장소(rximg/EfficientAD, models.py)의 PDN_S / PDN_M 과 동일 스펙입니다.
- Conv1/2: 4×4, stride 1, padding 3
- AvgPool1/2: 2×2, stride 2, padding 1
- (S) Conv3 3×3 p1, Conv4 4×4 p0 → last_kernel_size 채널
- (M) 1×1 → 3×3 → 4×4 → 1×1, 중간 채널 512 (Table 7)
"""

from __future__ import annotations

import torch.nn as nn
import torch.nn.functional as F


class PDN_S(nn.Module):
    """Table 6. Student는 last_kernel_size=768 (Conv-4 대신 384→768 커널)."""

    def __init__(
        self,
        last_kernel_size: int = 384,
        in_channels: int = 3,
        with_bn: bool = False,
    ) -> None:
        super().__init__()
        self.with_bn = with_bn
        self.last_kernel_size = last_kernel_size
        self.conv1 = nn.Conv2d(in_channels, 128, kernel_size=4, stride=1, padding=3)
        self.conv2 = nn.Conv2d(128, 256, kernel_size=4, stride=1, padding=3)
        self.conv3 = nn.Conv2d(256, 256, kernel_size=3, stride=1, padding=1)
        self.conv4 = nn.Conv2d(256, last_kernel_size, kernel_size=4, stride=1, padding=0)
        self.avgpool1 = nn.AvgPool2d(kernel_size=2, stride=2, padding=1)
        self.avgpool2 = nn.AvgPool2d(kernel_size=2, stride=2, padding=1)
        if self.with_bn:
            self.bn1 = nn.BatchNorm2d(128)
            self.bn2 = nn.BatchNorm2d(256)
            self.bn3 = nn.BatchNorm2d(256)
            self.bn4 = nn.BatchNorm2d(last_kernel_size)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x) if self.with_bn else x
        x = F.relu(x)
        x = self.avgpool1(x)
        x = self.conv2(x)
        x = self.bn2(x) if self.with_bn else x
        x = F.relu(x)
        x = self.avgpool2(x)
        x = self.conv3(x)
        x = self.bn3(x) if self.with_bn else x
        x = F.relu(x)
        x = self.conv4(x)
        x = self.bn4(x) if self.with_bn else x
        return x


class PDN_M(nn.Module):
    """Table 7. Student는 last_kernel_size=768."""

    def __init__(
        self,
        last_kernel_size: int = 384,
        in_channels: int = 3,
        with_bn: bool = False,
    ) -> None:
        super().__init__()
        self.with_bn = with_bn
        self.last_kernel_size = last_kernel_size
        self.conv1 = nn.Conv2d(in_channels, 256, kernel_size=4, stride=1, padding=3)
        self.conv2 = nn.Conv2d(256, 512, kernel_size=4, stride=1, padding=3)
        self.conv3 = nn.Conv2d(512, 512, kernel_size=1, stride=1, padding=0)
        self.conv4 = nn.Conv2d(512, 512, kernel_size=3, stride=1, padding=1)
        self.conv5 = nn.Conv2d(512, last_kernel_size, kernel_size=4, stride=1, padding=0)
        self.conv6 = nn.Conv2d(last_kernel_size, last_kernel_size, kernel_size=1, stride=1, padding=0)
        self.avgpool1 = nn.AvgPool2d(kernel_size=2, stride=2, padding=1)
        self.avgpool2 = nn.AvgPool2d(kernel_size=2, stride=2, padding=1)
        if self.with_bn:
            self.bn1 = nn.BatchNorm2d(256)
            self.bn2 = nn.BatchNorm2d(512)
            self.bn3 = nn.BatchNorm2d(512)
            self.bn4 = nn.BatchNorm2d(512)
            self.bn5 = nn.BatchNorm2d(last_kernel_size)
            self.bn6 = nn.BatchNorm2d(last_kernel_size)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x) if self.with_bn else x
        x = F.relu(x)
        x = self.avgpool1(x)
        x = self.conv2(x)
        x = self.bn2(x) if self.with_bn else x
        x = F.relu(x)
        x = self.avgpool2(x)
        x = self.conv3(x)
        x = self.bn3(x) if self.with_bn else x
        x = F.relu(x)
        x = self.conv4(x)
        x = self.bn4(x) if self.with_bn else x
        x = F.relu(x)
        x = self.conv5(x)
        x = self.bn5(x) if self.with_bn else x
        x = F.relu(x)
        x = self.conv6(x)
        x = self.bn6(x) if self.with_bn else x
        return x


class PatchDescriptionNetwork(nn.Module):
    """
    variant: 'S' | 'M'
    out_channels: Teacher 384, Student 768 (논문).
    in_channels: 열화상 1채널이면 1로 두고 첫 Conv 가 입력 채널에 맞춤 (권장: Grayscale→3채널 복제 후 3).
    """

    def __init__(
        self,
        variant: str = "S",
        out_channels: int = 384,
        in_channels: int = 3,
        with_bn: bool = False,
    ):
        super().__init__()
        self.variant = variant.upper()
        self.out_channels = out_channels
        self.in_channels = in_channels
        if self.variant == "M":
            self.pdn = PDN_M(last_kernel_size=out_channels, in_channels=in_channels, with_bn=with_bn)
        else:
            self.pdn = PDN_S(last_kernel_size=out_channels, in_channels=in_channels, with_bn=with_bn)

    def forward(self, x):
        return self.pdn(x)
