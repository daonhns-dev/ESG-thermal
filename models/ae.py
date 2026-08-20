"""
AutoEncoder for Thermal Image Anomaly Detection
열화상 이미지 이상탐지를 위한 오토인코더 구현
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from abc import ABC, abstractmethod
from typing import Optional, Tuple

try: 
    from torchvision import models as tv_models
    _TORCHVISION_AVAILABLE = True
except ImportError:
    _TORCHVISION_AVAILABLE = False

# ---------------------------------------------------------------------------
# Utility: Losses
# ---------------------------------------------------------------------------

def _gaussian_kernel(kernel_size: int, sigma: float, channels: int) -> torch.Tensor:
    x = torch.arange(kernel_size, dtype=torch.float32) - kernel_size // 2
    g = torch.exp(-x.pow(2) / (2 * sigma ** 2))
    g = g / g.sum()
    k2d = g.unsqueeze(1) @ g.unsqueeze(0)   
    return k2d.expand(channels, 1, kernel_size, kernel_size)


def _gaussian_blur2d(x: torch.Tensor, kernel_size: int = 5, sigma: float = 1.5,) -> torch.Tensor:
    """
    2D Gaussian blur (엣지 노이즈 제거 후 온도 분포만 비교용).
    열화상 anomaly score 계산 시 blur_then_diff 모드에서 사용.
    kernel_size는 홀수로만 사용 (짝수 입력 시 +1 보정).
    """
    if kernel_size % 2 == 0:
        kernel_size = kernel_size + 1
    C = x.size(1)
    k = _gaussian_kernel(kernel_size, sigma, C).to(x.device)
    pad = kernel_size // 2
    return F.conv2d(x, k, padding=pad, groups=C)


def _compute_anomaly_map_and_score(
    x: torch.Tensor,
    recon: torch.Tensor,
    score_mode: str = "pixel_mse",
    multi_scale: bool = True,
    smooth: bool = False,
    smooth_kernel: int = 5,
    blur_kernel_size: int = 5,
    blur_sigma: float = 1.5,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    이상 맵 및 이미지별 스칼라 스코어 계산 (공통 로직).

    score_mode:
      - "pixel_mse"           : (x - recon)^2 평균 (기존 방식)
      - "temperature_weighted": 고온 영역 가중. 배치 통계 사용 → 동일 절대 온도는 동일 weight
      - "blur_then_diff"      : Gaussian blur 후 차이 → 엣지가 아닌 온도 분포 차이만 측정
    """
    eps = 1e-6
    H, W = x.shape[-2], x.shape[-1]
    # blur/smooth 커널은 홀수만 허용 (짝수 시 +1 보정)
    blur_kernel_size = blur_kernel_size if blur_kernel_size % 2 == 1 else blur_kernel_size + 1
    smooth_kernel = smooth_kernel if smooth_kernel % 2 == 1 else smooth_kernel + 1

    if score_mode == "temperature_weighted":
        # per-image 통계 사용 -> 배치 구성(크기/내용)에 무관하게 이미지별 일관된 weight
        # flatten(1): (B, C*H*W) -> 이미지별 독립 통계
        mean = x.flatten(1).mean(dim=1).view(-1, 1, 1, 1)
        std = x.flatten(1).std(dim=1).view(-1, 1, 1, 1).clamp(min=eps)
        weight_map = torch.sigmoid((x - mean) / std)
        base_map = (x - recon).pow(2) * weight_map
    elif score_mode == "blur_then_diff":
        x_blur = _gaussian_blur2d(x, kernel_size=blur_kernel_size, sigma=blur_sigma)
        recon_blur = _gaussian_blur2d(recon, kernel_size=blur_kernel_size, sigma=blur_sigma)
        base_map = (x_blur - recon_blur).pow(2)
    else:
        base_map = (x - recon).pow(2)

    if multi_scale:
        anomaly_map = base_map.clone()
        for scale in [0.5, 0.25, 0.125]:
            h, w = max(int(H * scale), 4), max(int(W * scale), 4)
            xd = F.interpolate(x, (h, w), mode="bilinear", align_corners=False)
            rd = F.interpolate(recon, (h, w), mode="bilinear", align_corners=False)
            if score_mode == "blur_then_diff":
                xd = _gaussian_blur2d(xd, kernel_size=blur_kernel_size, sigma=blur_sigma)
                rd = _gaussian_blur2d(rd, kernel_size=blur_kernel_size, sigma=blur_sigma)
            eu = F.interpolate((xd - rd).pow(2), (H, W), mode="bilinear", align_corners=False)
            anomaly_map = anomaly_map + eu
    else:
        anomaly_map = base_map

    if smooth:
        C = anomaly_map.size(1)
        k = _gaussian_kernel(smooth_kernel, sigma=max(smooth_kernel / 3.0, 1e-3), channels=C).to(x.device)
        anomaly_map = F.conv2d(anomaly_map, k, padding=smooth_kernel // 2, groups=C)

    total_score = anomaly_map.flatten(1).mean(dim=1)
    return anomaly_map, total_score


def ssim_loss(x: torch.Tensor, y: torch.Tensor, kernel_size: int = 11, sigma: float = 1.5, data_range: float = 1.0, eps: float = 1e-8,) -> torch.Tensor:
    """
    SSIM 기반 손실 (1 - SSIM).
    채널별로 독립 계산 후 평균.

    Args:
        x, y : (B, C, H, W) tensor, value range [0, data_range]
        kernel_size: Gaussian window size
        sigma: Gaussian standard deviation
        data_range: Maximum pixel value
        eps: Numerical stability

    Returns:
        scalar loss value
    """
    C1 = (0.01 * data_range) ** 2
    C2 = (0.03 * data_range) ** 2
    B, C, H, W = x.shape

    kernel = _gaussian_kernel(kernel_size, sigma, channels=C).to(x.device)
    pad = kernel_size // 2

    mu_x = F.conv2d(x, kernel, padding=pad, groups=C)
    mu_y = F.conv2d(y, kernel, padding=pad, groups=C)
    mu_x2 = mu_x * mu_x
    mu_y2 = mu_y * mu_y
    mu_xy = mu_x * mu_y

    sg_x2 = F.conv2d(x * x, kernel, padding=pad, groups=C) - mu_x2
    sg_y2 = F.conv2d(y * y, kernel, padding=pad, groups=C) - mu_y2
    sg_xy = F.conv2d(x * y, kernel, padding=pad, groups=C) - mu_xy

    num = (2 * mu_xy + C1) * (2 * sg_xy + C2)
    den = (mu_x2 + mu_y2 + C1) * (sg_x2 + sg_y2 + C2) + eps

    ssim_map = num / den          # (B, C, H, W)
    return 1.0 - ssim_map.mean()

class PerceptualLoss(nn.Module):
    """
    VGG16 feature space 거리 기반 perceptual loss

    열화상(1ch) -> 3ch 복제후 VGG 통과.
    의미적 구조(형태, 질감) 보존을 강제 -> 재구성 품질 향상.
    torchvision 없으면 자동 비활성화.
    """

    def __init__(self, layer_ids: Tuple[int, ...] = (3, 8, 15)):
        super().__init__()
        self.enabled = _TORCHVISION_AVAILABLE
        if not self.enabled:
            return
        
        vgg = tv_models.vgg16(weights=tv_models.VGG16_Weights.DEFAULT).features
        self.slices = nn.ModuleList()
        prev = 0
        for lid in sorted(layer_ids):
            self.slices.append(vgg[prev:lid+1])
            prev = lid + 1

        for p in self.parameters():
            p.requires_grad_(False)

    def _to_3ch(self, t: torch.Tensor) -> torch.Tensor:
        if t.size(1) == 1:
            return t.repeat(1, 3, 1, 1)
        if t.size(1) == 4:
            return t[:, 1:4]
        return t

    def forward(self, recon: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        if not self.enabled:
            return torch.tensor(0.0, device=recon.device)

        r, t = self._to_3ch(recon), self._to_3ch(target)
        loss = torch.tensor(0.0, device=recon.device)
        for sl in self.slices:
            r = sl(r)
            t = sl(t)
            loss += F.mse_loss(r, t)
        return loss


# ===========================================================================
# Attention: CBAM
# ===========================================================================

class ChannelAttention(nn.Module):
    """어떤 특징 채널이 중요한지 학습 (Squeeze-and-Excitation)"""

    def __init__(self, channels: int, reduction: int = 16):
        super().__init__()
        mid = max(channels // reduction, 4)
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        self.mlp = nn.Sequential(
            nn.Flatten(),
            nn.Linear(channels, mid, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(mid, channels, bias=False),
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        scale = self.sigmoid(self.mlp(self.avg_pool(x)) + self.mlp(self.max_pool(x))).unsqueeze(-1).unsqueeze(-1)
        return x * scale

class SpatialAttention(nn.Module):
    """어디(공간 위치)가 중요한지 학습. 열화상 국소 이상 탐지에 효과적"""

    def __init__(self, kernel_size: int = 7):
        super().__init__()
        self.conv = nn.Conv2d(2, 1, kernel_size, padding=kernel_size // 2, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        avg = x.mean(dim=1, keepdim=True)
        mx, _ = x.max(dim=1, keepdim=True)
        return x * self.sigmoid(self.conv(torch.cat([avg, mx], dim=1)))

class CBAM(nn.Module):
    """Channel -> Spatial 순서로 어텐션 적용"""

    def __init__(self, channels: int, reduction: int = 16, spatial_kernel: int = 7):
        super().__init__()
        self.ca = ChannelAttention(channels, reduction)
        self.sa = SpatialAttention(spatial_kernel)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.sa(self.ca(x))


# ===========================================================================
# Building Blocks
# ===========================================================================

class ResidualBottleneck(nn.Module):
    """1x1 -> 3x3 -> 1x1 병목 + skip + CBAM attention"""

    def __init__(self, channels: int, bottleneck_ratio: float = 0.25, use_attention: bool = True):
        super().__init__()
        mid = max(int(channels * bottleneck_ratio), 1)
        self.block = nn.Sequential(
            nn.Conv2d(channels, mid, 1, bias=False),
            nn.BatchNorm2d(mid),
            nn.SiLU(inplace=True),
            nn.Conv2d(mid, mid, 3, padding=1, bias=False),
            nn.BatchNorm2d(mid),
            nn.SiLU(inplace=True),
            nn.Conv2d(mid, channels, 1, bias=False),
            nn.BatchNorm2d(channels),
        )
        self.attn = CBAM(channels) if use_attention else nn.Identity()
        self.act  = nn.SiLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.act(self.attn(x + self.block(x)))


def _encoder_block(in_ch: int, out_ch: int, use_attention: bool = True, dropout: float = 0.1,) -> nn.Sequential:
    """
    Encoder block에 작은 Dropout(기본 0.1)을 추가해 overfitting을 완화한다.
    """
    layers: list[nn.Module] = [
        nn.Conv2d(in_ch, out_ch, kernel_size=4, stride=2, padding=1, bias=False),
        nn.BatchNorm2d(out_ch),
        nn.SiLU(inplace=True),
    ]
    if dropout and dropout > 0.0:
        layers.append(nn.Dropout2d(dropout))
    layers.append(ResidualBottleneck(out_ch, use_attention=use_attention))
    return nn.Sequential(*layers)


def _decoder_block(in_ch: int, out_ch: int, final: bool = False) -> nn.Sequential:
    if final:
        return nn.Sequential(
            nn.ConvTranspose2d(in_ch, out_ch, kernel_size=4, stride=2, padding=1, bias=False),
            nn.Sigmoid(),
        )
    return nn.Sequential(
        nn.ConvTranspose2d(in_ch, out_ch, kernel_size=4, stride=2, padding=1, bias=False),
        nn.BatchNorm2d(out_ch),
        nn.SiLU(inplace=True),
    )


# ===========================================================================
# Base Class
# ===========================================================================

class BaseAutoEncoder(nn.Module, ABC):
    """공통 인터페이스. compute_loss / get_anomaly_score 중복 제거"""

    mse_weight : float = 0.8
    ssim_weight : float = 0.2
    perceptual_weight : float = 0.0
    ssim_warmup_epochs: int = 10

    @abstractmethod
    def encode(self, x: torch.Tensor) -> torch.Tensor: ...
    @abstractmethod
    def decode(self, x: torch.Tensor) -> torch.Tensor: ...

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        z = self.encode(x)
        return self.decode(z), z

    def compute_loss(self, x: torch.Tensor, recon: torch.Tensor, perceptual_loss_fn: Optional[PerceptualLoss] = None, epoch: int = 0,) -> torch.Tensor:
        """
        MSE + SSIM(warmup) + (선택) Perceptual 복합 손실.

        Args:
            x:                  (B, C, H, W) 원본 이미지
            recon:              (B, C, H, W) 재구성 이미지
            perceptual_loss_fn: (None이면 미사용) PerceptualLoss 인스턴스
            epoch:              현재 에폭 (warmup 적용 여부 결정)
        """
        # 수치 안정: recon이 [0,1] 밖이면 clamp (AMP 등으로 인한 오버플로우 방지)
        recon = recon.clamp(0.0, 1.0)

        # SSIM warmup: epoch에 따라 0.0 → ssim_weight 선형 증가
        warmup_ratio = min(1.0, epoch / max(self.ssim_warmup_epochs, 1))
        effective_ssim_w = self.ssim_weight * warmup_ratio
        # mse_weight 클래스 변수를 기준으로 하되, SSIM이 올라온 만큼 비례 축소
        # e.g. mse_weight=0.8, ssim_weight=0.2 → warmup 완료 시 0.8:0.2 유지
        total_w = self.mse_weight + effective_ssim_w
        effective_mse_w = self.mse_weight / max(total_w, 1e-8) * total_w

        mse  = F.mse_loss(recon, x)
        ssim = ssim_loss(recon, x)
        loss = effective_mse_w * mse + effective_ssim_w * ssim

        if perceptual_loss_fn is not None and self.perceptual_weight > 0:
            loss += self.perceptual_weight * perceptual_loss_fn(recon, x)

        # NaN/Inf 방지: SSIM 등에서 수치 불안정 시 MSE만 사용
        if not torch.isfinite(loss):
            loss = mse
        return loss
    
    @torch.no_grad()
    def get_anomaly_score(self, x: torch.Tensor, multi_scale: bool = True, smooth: bool = False, 
                          smooth_kernel: int = 5, score_mode: str = "pixel_mse", blur_kernel_size: int = 5, blur_sigma: float = 1.5,) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Multi-scale 이상 스코어 계산.

        score_mode:
          - "pixel_mse"           : 픽셀 MSE 평균 (기존)
          - "temperature_weighted": 고온 영역 가중 → 열화상 온도 편차 반영
          - "blur_then_diff"      : Blur 후 차이 → 엣지가 아닌 온도 분포 차이만 측정
        Args:
            x:                 입력 이미지 (B, C, H, W)
            multi_scale:       True이면 4개 스케일 오차 합산
            smooth:            True이면 anomaly map에 가우시안 스무딩
            smooth_kernel:     스무딩 커널 크기 (홀수)
            score_mode:        스코어 계산 방식 (pixel_mse | temperature_weighted | blur_then_diff)
            blur_kernel_size:  blur_then_diff 시 Gaussian 커널 크기
            blur_sigma:        blur_then_diff 시 Gaussian sigma

        Returns:
            anomaly_map:  픽셀별 이상 맵 (B, C, H, W)
            total_score:  배치당 스칼라 스코어 (B,)
        """
        was_training = self.training
        self.eval()
        recon, _ = self.forward(x)
        anomaly_map, total_score = _compute_anomaly_map_and_score(
            x,
            recon,
            score_mode=score_mode,
            multi_scale=multi_scale,
            smooth=smooth,
            smooth_kernel=smooth_kernel,
            blur_kernel_size=blur_kernel_size,
            blur_sigma=blur_sigma,
        )
        if was_training:
            self.train()
        return anomaly_map, total_score


# ===========================================================================
# ConvAutoEncoder — 메인 구현체
# ===========================================================================

class ConvAutoEncoder(BaseAutoEncoder):
    """
    Convolutional AutoEncoder with CBAM Attention + optional VAE.

    Args:
        input_channels: 입력 채널 수 (1=thermal, 3=RGB, 4=thermal+RGB)
        latent_dim:     잠재 채널 수
        base_channels:  첫 번째 레이어 채널 수 (이후 2x 증가, max 512)
        depth:          다운샘플 단계 수
        vae:            True이면 VAE 모드 (reparameterization + KL loss)
        use_attention:  False이면 CBAM 비활성화 (속도 우선 시)
    """
    
    def __init__(self, input_channels: int = 1, latent_dim: int = 128, base_channels: int = 32, depth: int = 5, vae: bool = False, use_attention: bool = True, spatial_latent: bool = False,):
        super().__init__()
        self.input_channels = input_channels
        self.latent_dim = latent_dim
        self.vae = vae
        self.spatial_latent = spatial_latent
        # kl_loss 호출 전 encode()가 없어도 안전하도록 기본값 초기화
        self._last_mu = torch.zeros(1)
        self._last_logvar = torch.zeros(1)

        ch = [input_channels] + [min(base_channels * (2 ** i), 512) for i in range(depth)]

        # --- Encoder ---
        self.enc_blocks = nn.ModuleList([_encoder_block(ch[i], ch[i + 1], use_attention=use_attention) for i in range(depth)])
        self.gap = nn.AdaptiveAvgPool2d(1)
        self.to_mu = nn.Conv2d(ch[-1], latent_dim, 1, bias = False)
        self.to_logvar = nn.Conv2d(ch[-1], latent_dim, 1, bias = False) if vae else None

        # --- Decoder ---
        # depth번 stride-2 down 후 gap(1×1) → from_latent로 복원할 때
        # 디코더가 depth번 ×2 업샘플 → 최종 해상도 = from_latent_size × 2^depth
        # depth=5 이면 8×8 → 256, depth=4이면 8×8 → 128이 되도록 kernel_size 자동 결정
        # target: 2^depth = input_size이므로 from_latent_size = input_size // 2^depth
        # kernel_size = from_latent_size (stride=1, padding=0)
        from_latent_size = max(1, 256 // (2 ** depth))
        if spatial_latent:
            self.from_latent = nn.Conv2d(latent_dim, ch[-1], kernel_size=1, bias=False)
        else:
            self.from_latent = nn.ConvTranspose2d(latent_dim, ch[-1], kernel_size=from_latent_size, stride=from_latent_size, padding=0, bias=False)
        dec_ch = list(reversed(ch))
        self.dec_blocks = nn.ModuleList([_decoder_block(dec_ch[i], dec_ch[i + 1], final=(i == depth - 1)) for i in range(depth)])

    def _reparameterize(self, mu: torch.Tensor, logvar: torch.Tensor) -> torch.Tensor:
        if self.training:
            std = torch.exp(0.5 * logvar)
            return mu + std * torch.randn_like(std)
        return mu

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        h = x
        for block in self.enc_blocks:
            h = block(h)
        if not self.spatial_latent:
            h = self.gap(h)
        mu = self.to_mu(h)
        if self.vae and self.to_logvar is not None:
            logvar = self.to_logvar(h)
            self._last_mu = mu
            self._last_logvar = logvar
            return self._reparameterize(mu, logvar)
        return mu

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        h = self.from_latent(z)
        for block in self.dec_blocks:
            h = block(h)
        return h

    def kl_loss(self) -> torch.Tensor:
        """VAE 모드 전용. encode() 호출 후 사용."""
        if not self.vae:
            return torch.tensor(0.0)
        if self._last_mu.shape == torch.Size([1]):
            raise RuntimeError("kl_loss()는 encode() 호출 이후에 사용해야 합니다.")
        mu, logvar = self._last_mu, self._last_logvar
        return -0.5 * torch.mean(1 + logvar - mu.pow(2) - logvar.exp())

    def compute_loss(self, x: torch.Tensor, recon: torch.Tensor, perceptual_loss_fn: Optional[PerceptualLoss] = None, kl_weight: float = 1e-4, epoch: int = 0,) -> torch.Tensor:
        """VAE 모드: MSE + SSIM + Perceptual + KL / AE 모드: MSE + SSIM + Perceptual"""
        loss = super().compute_loss(x, recon, perceptual_loss_fn, epoch=epoch)
        if self.vae:
            loss = loss + kl_weight * self.kl_loss()
        return loss


# ===========================================================================
# SimpleAutoEncoder — 경량 버전
# ===========================================================================

class SimpleAutoEncoder(BaseAutoEncoder):
    """
    경량 AutoEncoder - 빠른 실험 / 소형 모델 필요 시

    Bilinear upsample 사용으로 checkerboard artifact 없음.
    """
    
    def __init__(self, input_channels: int = 1, latent_dim: int = 64):
        super().__init__()
        ch = [input_channels, 16, 32, 64, latent_dim]

        self.enc_blocks = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(ch[i], ch[i + 1], 3, stride=2, padding=1, bias=False),
                nn.BatchNorm2d(ch[i+1]),
                nn.SiLU(inplace=True),
            )
            for i in range(len(ch) - 1)
        ])

        rev = list(reversed(ch))
        self.dec_blocks = nn.ModuleList()
        for i in range(len(rev) - 1):
            final = i == len(rev) - 2
            if final:
                self.dec_blocks.append(nn.Sequential(
                    nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False),
                    nn.Conv2d(rev[i], rev[i + 1], 3, padding=1, bias=False),
                    nn.Sigmoid(),
                ))
            else:
                self.dec_blocks.append(nn.Sequential(
                    nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False),
                    nn.Conv2d(rev[i], rev[i + 1], 3, padding=1, bias=False),
                    nn.BatchNorm2d(rev[i + 1]),
                    nn.SiLU(inplace=True),
                ))
    
    def encode(self, x: torch.Tensor) -> torch.Tensor:
        h = x
        for b in self.enc_blocks: 
            h = b(h)
        return h

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        h = z
        for b in self.dec_blocks:
            h = b(h)
        return h


# ===========================================================================
# ThermalRGBConvAE — Phase 2: 4채널
# ===========================================================================

class ThermalRGBConvAE(nn.Module):
    """Thermal + RGB 4채널 ConvAE (Phase 2). ConvAutoEncoder(input_channels=4) 래핑."""

    def __init__(
        self,
        latent_dim: int = 128,
        base_channels: int = 32,
        depth: int = 5,
        vae: bool = False,
        use_attention: bool = True,
    ):
        super().__init__()
        self._model = ConvAutoEncoder(
            input_channels=4,
            latent_dim=latent_dim,
            base_channels=base_channels,
            depth=depth,
            vae=vae,
            use_attention=use_attention,
        )

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        return self._model.encode(x)

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        return self._model.decode(z)

    def compute_loss(self, x, recon, perceptual_loss_fn=None, kl_weight=1e-4, epoch=0):
        return self._model.compute_loss(x, recon, perceptual_loss_fn, kl_weight=kl_weight, epoch=epoch)

    @torch.no_grad()
    def get_anomaly_score(self, x: torch.Tensor, multi_scale: bool = True, smooth: bool = False,
                          smooth_kernel: int = 5, score_mode: str = "pixel_mse", blur_kernel_size: int = 5, blur_sigma: float = 1.5,) -> Tuple[torch.Tensor, torch.Tensor]:
        return self._model.get_anomaly_score(
            x,
            multi_scale=multi_scale,
            smooth=smooth,
            smooth_kernel=smooth_kernel,
            score_mode=score_mode,
            blur_kernel_size=blur_kernel_size,
            blur_sigma=blur_sigma,
        )


# ===========================================================================
# MultiModalAE — Phase 3: Thermal + RGB + CSV
# ===========================================================================

class MultiModalAE(BaseAutoEncoder):
    """
    Thermal + RGB + CSV 멀티모달 AE (Phase 3)
    
    Args:
        image_channels: 이미지 채널 (기본 4)
        csv_dim:        CSV 피처 차원
        latent_dim:     공유 latent 차원
        fusion_type:    'gate' | 'concat' | 'add'
        vae:            VAE 모드
        use_attention:  CBAM 어텐션 사용 여부
    """
    
    def __init__(
        self,
        image_channels: int = 4,
        csv_dim: int = 10,
        latent_dim: int = 128,
        base_channels: int = 32,
        depth: int = 5,
        fusion_type: str = 'gate',
        vae: bool = False,
        use_attention: bool = True,
    ):
        super().__init__()
        assert fusion_type in ['gate', 'concat', 'add']
        self.fusion_type = fusion_type
        self.latent_dim = latent_dim

        self._img_ae = ConvAutoEncoder(
            input_channels=image_channels,
            latent_dim=latent_dim,
            base_channels=base_channels,
            depth=depth,
            vae=vae,
            use_attention=use_attention,
        )

        self.csv_encoder = nn.Sequential(
            nn.Linear(csv_dim, 64),
            nn.LayerNorm(64),
            nn.SiLU(),
            nn.Linear(64, 64),
            nn.LayerNorm(64),
            nn.SiLU(),
            nn.Linear(64, latent_dim)
        )
        
        if fusion_type == 'gate':
            self.gate = nn.Sequential(
                nn.Linear(latent_dim * 2, latent_dim),
                nn.Sigmoid(),
            )
        elif fusion_type == 'concat':
            self.fusion_proj = nn.Sequential(
                nn.Linear(latent_dim * 2, latent_dim),
                nn.SiLU(),
            )

        self._decoder_ae = ConvAutoEncoder(
            input_channels=image_channels,
            latent_dim=latent_dim,
            base_channels=base_channels,
            depth=depth,
        )
    
    def encode_image(self, x_img: torch.Tensor) -> torch.Tensor:
        return self._img_ae.encode(x_img)
    
    def encode_csv(self, x_csv: torch.Tensor) -> torch.Tensor:
        return self.csv_encoder(x_csv).unsqueeze(-1).unsqueeze(-1)
    
    def fuse(self, z_img: torch.Tensor, z_csv: torch.Tensor) -> torch.Tensor:
        zi = z_img.squeeze(-1).squeeze(-1)
        zc = z_csv.squeeze(-1).squeeze(-1)
        if self.fusion_type == "gate":
            alpha = self.gate(torch.cat([zi, zc], dim=1))
            return (alpha * zi + (1 - alpha) * zc).unsqueeze(-1).unsqueeze(-1)
        elif self.fusion_type == "concat":
            return self.fusion_proj(torch.cat([zi, zc], dim=1)).unsqueeze(-1).unsqueeze(-1)
        return ((zi + zc) / 2.0).unsqueeze(-1).unsqueeze(-1)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        return self._img_ae.encode(x)

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        return self._decoder_ae.decode(z)
    
    def forward(self, x_img: torch.Tensor, x_csv: Optional[torch.Tensor] = None,) -> Tuple[torch.Tensor, torch.Tensor]:
        z_img = self.encode_image(x_img)
        z = self.fuse(z_img, self.encode_csv(x_csv)) if x_csv is not None else z_img
        return self.decode(z), z
    
    def compute_loss(self, x_img: torch.Tensor, recon: torch.Tensor, x_csv: Optional[torch.Tensor] = None, perceptual_loss_fn: Optional[PerceptualLoss] = None, kl_weight: float = 1e-4, epoch: int = 0,) -> torch.Tensor:
        loss = super().compute_loss(x_img, recon, perceptual_loss_fn, epoch=epoch)
        if self._img_ae.vae:
            loss = loss + kl_weight * self._img_ae.kl_loss()
        return loss

    @torch.no_grad()
    def get_anomaly_score(self, x_img: torch.Tensor, x_csv: Optional[torch.Tensor] = None, multi_scale: bool = True, smooth: bool = False,
                          smooth_kernel: int = 5, score_mode: str = "pixel_mse", blur_kernel_size: int = 5, blur_sigma: float = 1.5,) -> Tuple[torch.Tensor, torch.Tensor]:
        was_training = self.training
        self.eval()
        recon, _ = self.forward(x_img, x_csv)
        anomaly_map, total_score = _compute_anomaly_map_and_score(
            x_img,
            recon,
            score_mode=score_mode,
            multi_scale=multi_scale,
            smooth=smooth,
            smooth_kernel=smooth_kernel,
            blur_kernel_size=blur_kernel_size,
            blur_sigma=blur_sigma,
        )
        if was_training:
            self.train()
        return anomaly_map, total_score

# ===========================================================================
# Sanity Check
# ===========================================================================

if __name__ == "__main__":
    def _p(m: nn.Module) -> str:
        return f"{sum(p.numel() for p in m.parameters()):,}"

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}\n")

    # 1. ConvAutoEncoder AE 모드
    print("=" * 60)
    print("[1] ConvAutoEncoder AE (1ch, attention=True)")
    m = ConvAutoEncoder(input_channels=1, latent_dim=128, use_attention=True).to(device)
    x = torch.randn(2, 1, 256, 256, device=device)
    recon, z = m(x)
    loss = m.compute_loss(x, recon)
    amap, sc = m.get_anomaly_score(x, multi_scale=True, smooth=True)
    print(f"  params={_p(m)} latent={tuple(z.shape)} loss={loss.item():.4f}")
    print(f"  anomaly_map={tuple(amap.shape)} score={sc.tolist()}")

    # 2. ConvAutoEncoder VAE 모드
    print("\n" + "=" * 60)
    print("[2] ConvAutoEncoder VAE (1ch)")
    mv = ConvAutoEncoder(input_channels=1, latent_dim=128, vae=True).to(device)
    rv, zv = mv(x)
    loss_v = mv.compute_loss(x, rv, kl_weight=1e-4)
    print(f"  params={_p(mv)} loss={loss_v.item():.4f} (MSE+SSIM+KL)")

    # 3. SimpleAutoEncoder
    print("\n" + "=" * 60)
    print("[3] SimpleAutoEncoder (1ch, 128x128)")
    sm = SimpleAutoEncoder(input_channels=1, latent_dim=64).to(device)
    xs = torch.randn(2, 1, 128, 128, device=device)
    rs, _ = sm(xs)
    print(f"  params={_p(sm)}  {tuple(xs.shape)} -> {tuple(rs.shape)}")

    # 4. ThermalRGBConvAE
    print("\n" + "=" * 60)
    print("[4] ThermalRGBConvAE (4ch)")
    m4 = ThermalRGBConvAE(latent_dim=128, use_attention=True).to(device)
    x4 = torch.randn(2, 4, 256, 256, device=device)
    r4, _ = m4(x4)
    print(f"  params={_p(m4)}  {tuple(x4.shape)} -> {tuple(r4.shape)}")

    # 5. MultiModalAE gated fusion
    print("\n" + "=" * 60)
    print("[5] MultiModalAE (4ch img + 10d csv, gated fusion)")
    mm = MultiModalAE(image_channels=4, csv_dim=10, latent_dim=128, fusion_type='gate').to(device)
    xi, xc = torch.randn(2, 4, 256, 256, device=device), torch.randn(2, 18, device=device)
    rm, zm = mm(xi, xc)
    lm = mm.compute_loss(xi, rm)
    am, sc = mm.get_anomaly_score(xi, xc, multi_scale=True)
    print(f"  params={_p(mm)}  loss={lm.item():.4f}")
    print(f"  anomaly_map={tuple(am.shape)} score={sc.tolist()}")

    # 6. PerceptualLoss 예시
    if _TORCHVISION_AVAILABLE:
        print("\n" + "=" * 60)
        print("[6] PerceptualLoss 포함 (MSE+SSIM+Perceptual)")
        perc = PerceptualLoss().to(device)
        xp = torch.randn(2, 1, 256, 256, device=device)
        mp = ConvAutoEncoder(input_channels=1, latent_dim=128).to(device)
        rp, _ = mp(xp)
        lp = mp.compute_loss(xp, rp, perceptual_loss_fn=perc)
        print(f"  loss={lp.item():.4f}")
    else:
        print("\n[6] torchvision 미설치 -> PerceptualLoss 비활성화")
        print("    pip install torchvision 으로 활성화 가능")

