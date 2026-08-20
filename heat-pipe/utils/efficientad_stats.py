"""
EfficientAD Algorithm 1 (3–10행): Teacher 출력 채널별 mean / std 추정.
전체 train 이미지에 대해 한 번 계산 후 model.set_teacher_feature_normalization 에 전달.
"""

from __future__ import annotations

from typing import Tuple

import torch
import torch.nn as nn

from models.efficientad_norm import imagenet_normalize


def _ensure_three_channel(x: torch.Tensor) -> torch.Tensor:
    if x.shape[1] == 1:
        return x.repeat(1, 3, 1, 1)
    return x


@torch.no_grad()
def compute_teacher_output_channel_stats(
    teacher_pdn: nn.Module,
    data_loader,
    device: torch.device,
    max_batches: int | None = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Args:
        teacher_pdn: PatchDescriptionNetwork (동결). 입력은 imagenet_normalize 적용 후 전달.
        data_loader: 배치 (x, ...) x 는 [0,1], 보통 (B,3,H,W) 또는 (B,1,H,W).
    Returns:
        mean, std 각 (C,) float tensor (CPU).
    """
    teacher_pdn.eval()

    # Chan의 병렬 Welford 알고리즘 — 배치 단위 수치 안정 분산 추정
    # 배치 내 통계: (count_b, mean_b, M2_b) 를 누적 통계와 병합.
    # 참조: Chan et al. (1979) "Updating Formulae and a Pairwise Algorithm for
    #        Computing Sample Variances"
    n_total: int = 0
    mean_acc: torch.Tensor | None = None
    M2_acc: torch.Tensor | None = None
    n_batch = 0

    for batch in data_loader:
        x = batch[0] if isinstance(batch, (list, tuple)) else batch
        x = _ensure_three_channel(x.to(device, non_blocking=True))
        feat = teacher_pdn(imagenet_normalize(x))
        b, c, fh, fw = feat.shape
        flat = feat.permute(1, 0, 2, 3).reshape(c, -1)  # (C, N_batch)

        n_b = flat.shape[1]
        mean_b = flat.mean(dim=1)
        M2_b = ((flat - mean_b.unsqueeze(1)) ** 2).sum(dim=1)

        if mean_acc is None:
            mean_acc = mean_b
            M2_acc = M2_b
            n_total = n_b
        else:
            # 두 집합 A(n_total, mean_acc, M2_acc)와 B(n_b, mean_b, M2_b) 병합
            n_new = n_total + n_b
            delta = mean_b - mean_acc
            mean_acc = mean_acc + delta * (n_b / n_new)
            M2_acc = M2_acc + M2_b + delta ** 2 * (n_total * n_b / n_new)
            n_total = n_new

        n_batch += 1
        if max_batches is not None and n_batch >= max_batches:
            break

    if mean_acc is None or n_total == 0:
        raise RuntimeError("Teacher 통계 계산: 데이터가 비었습니다.")

    var = M2_acc / n_total
    std = torch.sqrt(var.clamp(min=1e-12))
    return mean_acc.cpu(), std.cpu()
