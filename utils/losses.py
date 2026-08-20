"""
EfficientAD 관련 손실:

hard_feature_loss (Section 3.2):
    D_{c,w,h} = (T(I)_{c,w,h} - S(I)_{c,w,h})^2   ← 채널별 요소 단위
    d_hard = p_hard-quantile of ALL elements of D    ← C×W×H 전체에서 분위수
    L_hard = mean of all D_{c,w,h} >= d_hard
 
pretraining_penalty (Section 3.2):
    L_penalty = (C·W·H)^{-1} Σ_c ||S(P)_c||²_F
    ※ Teacher 출력과 비교 안 함! Student 출력 자체를 0으로 밀어 OOD 일반화 방지.
 
ae_loss (Section 3.3):
    L_AE = (C·W·H)^{-1} Σ_c ||T_norm(I)_c - A(I)_c||²_F
 
stae_loss (Section 3.3):
    L_STAE = (C·W·H)^{-1} Σ_c ||A(I)_c - S'(I)_c||²_F
"""

from __future__ import annotations

from typing import Optional

import torch
import torch.nn.functional as F


def hard_feature_loss(student_out: torch.Tensor, teacher_out: torch.Tensor, p_hard: float = 0.999,
                      roi_mask: Optional[torch.Tensor] = None,) -> torch.Tensor:
    """
    C×H×W 전체 요소에서 p_hard 분위수를 구하고, 그 이상인 요소만 평균.

    Args:
        student_out: Y_ST (B, C, H, W) — Student 전반 384ch
        teacher_out: T_norm (B, C, H, W) — 채널 정규화된 Teacher 출력
        p_hard: mining factor (0.999 → 상위 0.1%만 역전파)
        roi_mask: (B, 1, H, W) 0/1 마스크. 주어지면 ROI 내부 요소에서만
                  분위수·hard mining 수행 (학습 시점 ROI 실험용). None이면 기존 동작.
    """
    if not (0.0 < p_hard <= 1.0):
        raise ValueError(f"p_hard 는 (0, 1] 이어야 합니다. 받은 값: {p_hard}")
    # D_{c, h, w} = (T -S)^2  shape: (B, C, H, W)
    D = (teacher_out.detach() - student_out).pow(2)
    B = D.shape[0]
    loss = torch.tensor(0.0, device=D.device, dtype=D.dtype)

    for i in range(B):
        d_i = D[i]  # (C, H, W)
        if roi_mask is not None:
            sel = roi_mask[i].expand_as(d_i).reshape(-1) > 0.5
            d_flat = d_i.reshape(-1)[sel]
            if d_flat.numel() == 0:            # ROI가 비면 전체로 폴백
                d_flat = d_i.reshape(-1)
        else:
            d_flat = d_i.reshape(-1)
        d_hard = torch.quantile(d_flat, p_hard)
        mask = d_flat >= d_hard
        if mask.any():
            loss = loss + d_flat[mask].mean()
        else:
            loss = loss + d_flat.mean()

    return loss / B


def pretraining_penalty(student_out: torch.Tensor) -> torch.Tensor:
    """
    L_penalty = (C·W·H)^{-1} Σ_c ||S(P)_c||²_F
 
    ※ Teacher 출력과 비교하지 않음!
    ImageNet 이미지 P에서 Student 전체(768ch) 출력을 0으로 밀어넣어 OOD 일반화 방지.
 
    Args:
        student_out: S(P) (B, 768, H, W) — ImageNet 이미지에 대한 Student 전체 출력
    """
    return student_out.pow(2).mean()


def _masked_mse(diff2: torch.Tensor, roi_mask: Optional[torch.Tensor]) -> torch.Tensor:
    """roi_mask(B,1,H,W)가 주어지면 ROI 내부 평균, 아니면 전체 평균."""
    if roi_mask is None:
        return diff2.mean()
    m = roi_mask.expand_as(diff2)
    return (diff2 * m).sum() / m.sum().clamp(min=1.0)


def ae_loss(teacher_out: torch.Tensor, ae_out: torch.Tensor,
            roi_mask: Optional[torch.Tensor] = None,) -> torch.Tensor:
    """
    L_AE = MSE(T_norm, A).  roi_mask 주어지면 ROI 내부에서만 평균.

    Args:
        teacher_out: 채널 정규화된 Teacher 출력 (B, 384, 64, 64) — detach됨
        ae_out: A(I) (B, 384, 64, 64) — AE 출력
        roi_mask: (B, 1, 64, 64) 0/1 마스크 (학습 시점 ROI 실험용). None이면 기존 동작.
    """
    return _masked_mse((teacher_out.detach() - ae_out).pow(2), roi_mask)


def stae_loss(ae_out: torch.Tensor, student_stae_out: torch.Tensor,
              roi_mask: Optional[torch.Tensor] = None,) -> torch.Tensor:
    """
    L_STAE = MSE(A, S').  roi_mask 주어지면 ROI 내부에서만 평균.

    Args:
        ae_out: AE 출력 (B, 384, 64, 64) — AE gradient는 L_AE에서만 흐름
        student_stae_out: Student 후반 384ch 출력 (B, 384, 64, 64)
        roi_mask: (B, 1, 64, 64) 0/1 마스크 (학습 시점 ROI 실험용). None이면 기존 동작.
    """
    return _masked_mse((ae_out.detach() - student_stae_out).pow(2), roi_mask)
