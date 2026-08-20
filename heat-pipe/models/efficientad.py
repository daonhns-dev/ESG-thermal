"""
EfficientAD 통합: Teacher PDN(384) + Student PDN(768) + AE(Table 8).

- Student: Y_ST = f_s[:, :C], Y_STAE = f_s[:, C:] (C=Teacher 채널, 논문 384+384).
- Teacher 출력은 학습 전 train 전체에 대해 채널별 mean/std 로 정규화 (Algorithm 1).
- 별도 1×1 projection 없음 (공식과 동일).

Anomaly Map 계산 (Algorithm 2):
  local  = mean_c( (T_norm - Y_ST)^2 )     ← squared diff의 채널 평균
  global = mean_c( (A - Y_STAE)^2 )
  각 맵을 독립 분위수(q_a, q_b)로 정규화 후 0.5:0.5 가중 합산.
"""

from __future__ import annotations

from typing import Dict

import torch
import torch.nn as nn
import torch.nn.functional as F

from .efficientad_ae import EfficientADAutoEncoder
from .efficientad_norm import imagenet_normalize
from .pdn import PatchDescriptionNetwork


class EfficientAD(nn.Module):
    def __init__(
        self,
        teacher: PatchDescriptionNetwork,
        student: PatchDescriptionNetwork,
        autoencoder: EfficientADAutoEncoder,
    ):
        super().__init__()
        c_t = teacher.out_channels
        c_s = student.out_channels
        if c_s != 2 * c_t:
            raise ValueError(
                f"Student out_channels({c_s}) must be 2 × Teacher out_channels({c_t}) "
                "for Y_ST / Y_STAE split (384+384)."
            )
        self.teacher = teacher
        self.student = student
        self.autoencoder = autoencoder
        self.half_c = c_t

        for p in self.teacher.parameters():
            p.requires_grad = False

        # Teacher 채널 정규화
        self.register_buffer("teacher_feat_mu", torch.zeros(1, c_t, 1, 1))
        self.register_buffer("teacher_feat_sigma", torch.ones(1, c_t, 1, 1))

        # 분위수 맵 정규화 — local/global 각각 독립
        self.register_buffer("q_a_st", torch.tensor(0.0))
        self.register_buffer("q_b_st", torch.tensor(1.0))
        self.register_buffer("q_a_ae", torch.tensor(0.0))
        self.register_buffer("q_b_ae", torch.tensor(1.0))
        self.register_buffer("calibrated", torch.tensor(0, dtype=torch.uint8))

        # 스코어 집계 파라미터 (inference-time, 재학습 불필요)
        # score_alpha: local 가중치 (0~1). combined = alpha*local + (1-alpha)*global
        # _score_agg_mode: 0=max, 1=topk_mean, 2=mean
        # _score_topk_ratio: topk_mean 시 상위 몇 % 픽셀 평균
        self.register_buffer("score_alpha", torch.tensor(0.5))
        self.register_buffer("_score_agg_mode", torch.tensor(0, dtype=torch.uint8))
        self.register_buffer("_score_topk_ratio", torch.tensor(0.01))

    # =================================================================
    # Teacher 채널 정규화 설정
    # =================================================================
    def set_teacher_feature_normalization(self, mu: torch.Tensor, sigma: torch.Tensor) -> None:
        """mu, sigma: shape (C,) or (1,C,1,1) — Algorithm 1 channel normalization."""
        if mu.ndim == 1:
            mu = mu.view(1, -1, 1, 1)
        if sigma.ndim == 1:
            sigma = sigma.view(1, -1, 1, 1)
        self.teacher_feat_mu = mu.to(self.teacher_feat_mu.device)
        self.teacher_feat_sigma = torch.clamp(sigma.to(self.teacher_feat_sigma.device), min=1e-8)

    def _normalize_teacher(self, feat: torch.Tensor) -> torch.Tensor:
        return (feat - self.teacher_feat_mu) / self.teacher_feat_sigma

    # =================================================================
    # 스코어 집계 설정 (inference-time, 재학습 불필요)
    # =================================================================
    def set_score_params(self, alpha: float = 0.5, agg: str = "max", topk_ratio: float = 0.01) -> None:
        """
        이미지 스코어 집계 방식 설정.

        Args:
            alpha:      local 브랜치 가중치 (0.0~1.0). combined = alpha*local + (1-alpha)*global
            agg:        집계 방식 — "max" | "topk_mean" | "mean"
            topk_ratio: "topk_mean" 시 상위 몇 % 픽셀 (예: 0.01 = 상위 1%)
        """
        _agg_map = {"max": 0, "topk_mean": 1, "mean": 2}
        if agg not in _agg_map:
            raise ValueError(f"agg must be one of {list(_agg_map.keys())}, got '{agg}'")
        self.score_alpha.fill_(float(alpha))
        self._score_agg_mode.fill_(_agg_map[agg])
        self._score_topk_ratio.fill_(float(topk_ratio))

    def _aggregate_score(self, flat: torch.Tensor) -> torch.Tensor:
        """
        (B, N) 픽셀 텐서 → (B,) 이미지 스코어.

        mode 0 (max):       단일 최댓값 — 노이즈에 민감, 국소 이상 강조
        mode 1 (topk_mean): 상위 k% 픽셀 평균 — 노이즈 억제, FP 감소 기대
        mode 2 (mean):      전체 평균 — 가장 보수적
        """
        mode = self._score_agg_mode.item()
        if mode == 1:  
            k = max(1, int(flat.shape[1] * self._score_topk_ratio.item()))
            return flat.topk(k, dim=1).values.mean(dim=1)
        elif mode == 2:  
            return flat.mean(dim=1)
        else:  
            return flat.max(dim=1).values

    def _quantile_normalize(self, m: torch.Tensor, q_low: torch.Tensor, q_high: torch.Tensor, eps: float = 1e-8,) -> torch.Tensor:
        """q_a 지점 → 0, q_b 지점 → 0.1 로 선형 매핑."""
        ql = q_low.to(device=m.device, dtype=m.dtype).view(1, 1, 1, 1)
        qh = q_high.to(device=m.device, dtype=m.dtype).view(1, 1, 1, 1)
        denom = (qh - ql).clamp_min(eps)
        return 0.1 * (m - ql) / denom

    # =================================================================
    # 분위수 맵 정규화 설정 (Algorithm 1, lines 44-57)
    # =================================================================        
    @torch.no_grad()
    def set_quantiles_from_maps(self, local_scores: torch.Tensor, global_scores: torch.Tensor, q_a: float, q_b: float) -> None:
        """
        Validation 이미지의 local/global anomaly score를 받아 분위수 기준 계산.
 
        Args:
            local_scores: (N_pixels,) 전체 validation local map 값 flatten
            global_scores: (N_pixels,) 전체 validation global map 값 flatten
            q_a, q_b: 분위수 위치 (0.9, 0.995)
        """
        dev = local_scores.device
        qa_t = torch.tensor(q_a, device=dev, dtype=local_scores.dtype)
        qb_t = torch.tensor(q_b, device=dev, dtype=local_scores.dtype)

        # [#3 수정] register_buffer로 등록 값은 .copy_()로 갱신해야
        # state_dict 추적이 유지되고 .to(device) 이동 시 함께 이동함.
        # 직접 대입(self.q_a_st = ...)은 새 텐서를 생성해 buffer 추적에서 벗어남
        self.q_a_st.copy_(torch.quantile(local_scores, qa_t))
        self.q_b_st.copy_(torch.quantile(local_scores, qb_t))
        self.q_a_ae.copy_(torch.quantile(global_scores, qa_t))
        self.q_b_ae.copy_(torch.quantile(global_scores, qb_t))
        self.calibrated.fill_(1)


    # =================================================================
    # Forward — 추론 (Algorithm 2)
    # =================================================================
    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        _, _, h, w = x.shape
        xn = imagenet_normalize(x)

        # [#1 수정] _normalize_teacher 호출을 no_grad 블록 안으로 이동.
        # Teacher는 requires_grad=False이지만, _normalize_teacher의 
        # 뺄셈/나눗셈 연산까지 grad graph를 만들 필요가 없음.
        with torch.no_grad():
            f_t = self._normalize_teacher(self.teacher(xn))

        # Student -- 384 + 384 slice
        f_s = self.student(xn)
        y_st   = f_s[:, : self.half_c]
        y_stae = f_s[:, self.half_c :]

        # Raw 64×64 이상 맵 (PDN 출력 해상도, 정규화 전)
        local_map_raw = (f_t - y_st).pow(2).mean(dim=1, keepdim=True)

        # AE -> feature map (384 ch)
        ae_out = self.autoencoder(x)

        global_map_raw = (ae_out - y_stae).pow(2).mean(dim=1, keepdim=True)

        # 분위수 정규화: 64×64에서 적용 (학습 시 통계 추정과 동일 해상도·공간)
        if self.calibrated.item() == 1:
            local_norm  = self._quantile_normalize(local_map_raw, self.q_a_st, self.q_b_st)
            global_norm = self._quantile_normalize(global_map_raw, self.q_a_ae, self.q_b_ae)
        else:
            local_norm  = local_map_raw
            global_norm = global_map_raw

        # 64×64 기준 합산 및 이미지 스코어
        alpha = float(self.score_alpha.item())
        combined_64  = alpha * local_norm + (1.0 - alpha) * global_norm
        local_score  = self._aggregate_score(local_norm.flatten(1))
        global_score = self._aggregate_score(global_norm.flatten(1))
        image_score  = self._aggregate_score(combined_64.flatten(1))

        # 시각화용: 입력 해상도로 bilinear 업샘플 (64×64 → H×W)
        fh, fw = local_map_raw.shape[-2:]
        if (h, w) != (fh, fw):
            local_map    = F.interpolate(local_norm, size=(h, w), mode="bilinear", align_corners=False)
            global_map   = F.interpolate(global_norm, size=(h, w), mode="bilinear", align_corners=False)
            combined_map = F.interpolate(combined_64, size=(h, w), mode="bilinear", align_corners=False)
        else:
            local_map = local_norm
            global_map = global_norm
            combined_map = combined_64

        return {
            "y_st": y_st,
            "y_stae": y_stae,
            "f_t_norm": f_t,
            "ae_features": ae_out,
            "local_map_raw": local_map_raw,    # (B,1,64,64) 정규화 전 raw 맵
            "global_map_raw": global_map_raw,  # (B,1,64,64) 정규화 전 raw 맵
            "local_map": local_map,            # (B,1,H,W) 정규화 후 업샘플 (시각화용)
            "global_map": global_map,          # (B,1,H,W) 정규화 후 업샘플 (시각화용)
            "combined_map": combined_map,      # (B,1,H,W) 업샘플 (시각화용)
            "local_score": local_score,        # (B,) local 단독 이미지 스코어
            "global_score": global_score,      # (B,) global 단독 이미지 스코어
            "image_score": image_score,        # (B,) combined 이미지 스코어
        }

    # =================================================================
    # Forward — 학습 (Algorithm 1 한 iteration용)
    # =================================================================
    def forward_train(self, x_train: torch.Tensor, x_augmented: torch.Tensor) -> Dict[str, torch.Tensor]:
        """
        Args:
            x_train: 원본 학습 이미지 (S-T 학습용, augmentation 없음)
            x_augmented: augmented 이미지 (AE 학습용)
 
        Returns:
            f_t:     Teacher 출력(정규화됨) on x_train
            f_st:    Student 전반 384ch on x_train
            f_ae:    AE 출력 on x_augmented
            f_t_aug: Teacher 출력(정규화됨) on x_augmented
            f_stae:  Student 후반 384ch on x_augmented
        """
        tc = self.half_c
        xn_train = imagenet_normalize(x_train)
        xn_aug   = imagenet_normalize(x_augmented)
        
        # --- 원본 이미지: S-T 학습 -----
        # [#1 수정] _normalize_teacher 호출을 no_grad 블록 안으로 이동.
        with torch.no_grad():
            f_t = self._normalize_teacher(self.teacher(xn_train))
        f_s_train = self.student(xn_train)
        f_st = f_s_train[:, :tc]

        # --- augmented 이미지: AE + STAE 학습 ---
        # [#1 수정] 동일하게 no_grad 블록 통합.
        with torch.no_grad():
            f_t_aug = self._normalize_teacher(self.teacher(xn_aug))
        f_ae   = self.autoencoder(x_augmented)
        f_stae = self.student(xn_aug)[:, tc:]

        return {
            "f_t": f_t,
            "f_st": f_st,
            "f_ae": f_ae,
            "f_t_aug": f_t_aug,
            "f_stae": f_stae,            
        }



    # =================================================================
    # Factory
    # =================================================================
    @staticmethod
    def build_default(variant: str = "S", in_channels: int = 3, teacher_out: int = 384, student_out: int = 768, with_bn: bool = False,) -> "EfficientAD":
        # 인자 정렬 - teacher/student/ae 생성 스타일 통일
        teacher = PatchDescriptionNetwork(
            variant=variant,
            out_channels=teacher_out,
            in_channels=in_channels,
            with_bn=with_bn,
        )
        student = PatchDescriptionNetwork(
            variant=variant,
            out_channels=student_out,
            in_channels=in_channels,
            with_bn=with_bn,
        )
        ae = EfficientADAutoEncoder(
            in_channels=in_channels,
            is_bn=False,
            ae_feature_channels=teacher_out,
        )
        return EfficientAD(teacher=teacher, student=student, autoencoder=ae)
