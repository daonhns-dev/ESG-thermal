"""
EfficientAD 공식 사전학습 가중치 다운로드/로드 유틸.

공식 저장소: https://github.com/nelson1425/EfficientAD
증류된 Teacher PDN 가중치를 로컬에 캐시하여 Algorithm 3 증류를 생략할 수 있게 함.

사용법:
    from utils.efficientad_weights import load_pretrained_teacher

    teacher_state = load_pretrained_teacher(
        variant="S",
        cache_dir="results/checkpoints/efficientad",
    )
    model.teacher.load_state_dict(teacher_state)

주의:
    아래 URL이 작동하지 않을 경우 다음 방법을 사용하세요:
      1) distill_pdn.py 로 직접 증류
      2) 공식 저장소(nelson1425/EfficientAD)에서 수동 다운로드 후
         configs/config_efficientad.yaml 의 teacher_checkpoint 경로에 배치
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import torch

# 공식 저장소(nelson1425/EfficientAD) 기준 URL.
# 릴리즈 페이지에서 최신 URL을 확인 후 갱신하세요.
_WEIGHT_URLS = {
    "S": "https://github.com/nelson1425/EfficientAD/releases/download/v1.0/teacher_pdn_s.pth",
    "M": "https://github.com/nelson1425/EfficientAD/releases/download/v1.0/teacher_pdn_m.pth",
}


def download_file(
    url: str,
    dest: Path,
    expected_hash: Optional[str] = None,
    max_retries: int = 3,
) -> Path:
    """URL에서 파일 다운로드. 실패 시 지수 백오프로 max_retries 회 재시도."""
    import time

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        print(f"  캐시 사용: {dest}")
        return dest

    print(f"  다운로드: {url}")
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            torch.hub.download_url_to_file(url, str(dest), progress=True)
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            dest.unlink(missing_ok=True)
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"  다운로드 실패 (시도 {attempt}/{max_retries}), {wait}s 후 재시도: {e}")
                time.sleep(wait)

    if last_exc is not None:
        raise RuntimeError(
            f"Teacher 가중치 다운로드 실패 ({max_retries}회 시도): {last_exc}\n"
            f"수동 다운로드 후 {dest} 에 배치하세요.\n"
            f"또는 distill_pdn.py 로 직접 증류하세요."
        ) from last_exc

    if expected_hash:
        h = hashlib.sha256(dest.read_bytes()).hexdigest()[:16]
        if h != expected_hash[:16]:
            print(f"  ⚠ 해시 불일치: {h} != {expected_hash[:16]}")

    return dest


def load_pretrained_teacher(
    variant: str = "S",
    cache_dir: str = "results/checkpoints/efficientad",
    url_override: Optional[str] = None,
) -> dict:
    """
    공식 증류 Teacher PDN 가중치를 다운로드하고 state_dict를 반환.

    Args:
        variant: "S" | "M"
        cache_dir: 로컬 캐시 디렉토리
        url_override: 직접 URL 지정 (공식 URL 대신)

    Returns:
        state_dict: Teacher PDN의 state_dict
    """
    var = variant.upper()
    url = url_override or _WEIGHT_URLS.get(var)
    if not url:
        raise ValueError(f"Unknown variant: {var}. Available: {list(_WEIGHT_URLS.keys())}")

    filename = f"teacher_pdn_{var.lower()}.pth"
    cache_path = Path(cache_dir) / filename

    download_file(url, cache_path)
    data = torch.load(cache_path, map_location="cpu", weights_only=True)

    # 공식 가중치 형태에 따라 분기
    if isinstance(data, dict):
        if "teacher" in data:
            return data["teacher"]
        if "state_dict" in data:
            return data["state_dict"]
        # key가 모델 파라미터 이름으로 시작하면 그 자체가 state_dict
        first_key = next(iter(data.keys()), "")
        if "conv" in first_key or "pdn" in first_key or "body" in first_key:
            return data
    # fallback
    return data


def load_checkpoint(
    path: str,
    model: torch.nn.Module,
    device: str = "cpu",
) -> dict:
    """
    train_efficientad.py 가 저장한 체크포인트를 로드.

    Returns:
        checkpoint dict (iteration, optimizer 등 메타 정보 포함)
    """
    ckpt = torch.load(path, map_location=device, weights_only=False)
    model.teacher.load_state_dict(ckpt["teacher"])
    model.student.load_state_dict(ckpt["student"])
    model.autoencoder.load_state_dict(ckpt["autoencoder"])

    # 정규화 버퍼 복원
    if "teacher_feat_mu" in ckpt:
        model.set_teacher_feature_normalization(
            ckpt["teacher_feat_mu"].to(device),
            ckpt["teacher_feat_sigma"].to(device),
        )
    for key in ["q_a_st", "q_b_st", "q_a_ae", "q_b_ae", "calibrated"]:
        if key in ckpt:
            getattr(model, key).copy_(ckpt[key].to(device))

    print(f"체크포인트 로드: {path} (iteration={ckpt.get('iteration', '?')})")
    return ckpt