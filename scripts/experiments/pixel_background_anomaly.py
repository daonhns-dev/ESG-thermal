"""
플랜트 CCTV 카메라별 "픽셀 위치" 배경모델 기반 이상탐지 (2026-07-24).

가정: 같은 (facility, camera_id)의 프레임들은 고정 화각(팬/틸트/줌 없는 CCTV) —
실제로 0082/cctv1, 0087/cctv3 육안 비교에서 배경 구조가 동일했던 걸 확인함(대화 기록).
파일명 규칙(facility-accident_<event>_thermal_<facility>_<camera>_<frame>.png)에서
facility/camera를 파싱해 그룹화하고, 각 그룹의 정상(train+val) 프레임들로
"픽셀별" mean/std 배경모델을 학습한 뒤, 같은 그룹의 test 프레임을 z-score로 채점.

지금까지 시도한 것과의 차이:
  - brightness_baseline_auc.py: 이미지 전체 1개 통계값(전역) -> AUC~0.5(무신호)
  - visualize_local_anomaly_map.py: 프레임 "내부" 상대비교(같은 사진 안에서 튀는 곳)
  - 이 스크립트: 같은 물리적 픽셀 위치를 "시간축"으로 비교 (배경차분 방식)

사용법 (thermal/image/ 에서, GPU 불필요):
    python scripts/experiments/pixel_background_anomaly.py
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.metrics import roc_auc_score

DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "plant"
IMG_SIZE = 256
MIN_GROUP_FRAMES = 4    # leave-one-out 하려면 그룹당 정상 프레임이 최소 이 정도는 있어야 함
STD_FLOOR = 5.0         # 배경 자체가 완전히 고정이라 std~0인 픽셀에서 노이즈로 z-score 폭주 방지
TOPK_RATIO = 0.05       # 상위 5% |z| 픽셀 평균을 이미지 스코어로 사용


def parse_key(path: Path) -> str | None:
    parts = path.stem.split("_")
    if len(parts) < 3:
        return None
    facility, camera = parts[-3], parts[-2]
    return f"{facility}_{camera}"


def load_gray(path: Path) -> np.ndarray:
    return np.array(
        Image.open(path).convert("L").resize((IMG_SIZE, IMG_SIZE), Image.BILINEAR),
        dtype=np.float32,
    )


def collect(dirs: list[Path]) -> dict[str, list[Path]]:
    by_key: dict[str, list[Path]] = defaultdict(list)
    for d in dirs:
        for p in sorted(Path(d).glob("*.png")):
            key = parse_key(p)
            if key:
                by_key[key].append(p)
    return by_key


def topk_mean_abs(z: np.ndarray, ratio: float) -> float:
    flat = np.abs(z).ravel()
    k = max(1, int(len(flat) * ratio))
    idx = np.argpartition(flat, -k)[-k:]
    return float(flat[idx].mean())


def main():
    # normal은 train/val/test 폴더 구분 없이 그룹별로 다 모아서 "정상 프레임 풀"로 사용.
    # (leave-one-out으로 배경 대비 정상 스코어를 만들 것이므로, 어느 split에서 왔는지는 무관)
    normal_by_key = collect([
        DATA_ROOT / "train" / "normal",
        DATA_ROOT / "val" / "normal",
        DATA_ROOT / "test" / "normal",
    ])
    test_anom_by_key = collect([DATA_ROOT / "test" / "anomaly"])

    all_keys = sorted(set(normal_by_key) | set(test_anom_by_key))
    scores: list[float] = []
    labels: list[int] = []
    usable_keys, skipped_keys = 0, 0
    group_frame_counts: list[int] = []
    per_group_rows: list[dict] = []  # (key, n_normal, n_anom, group_auc)

    for key in all_keys:
        normal_paths = normal_by_key.get(key, [])
        anom_paths = test_anom_by_key.get(key, [])
        if len(normal_paths) < MIN_GROUP_FRAMES or not anom_paths:
            skipped_keys += 1
            continue
        usable_keys += 1
        group_frame_counts.append(len(normal_paths))

        normal_imgs = [load_gray(p) for p in normal_paths]
        normal_stack = np.stack(normal_imgs)
        n = len(normal_imgs)

        group_scores: list[float] = []
        group_labels: list[int] = []

        # leave-one-out: 프레임 i는 "나머지(i 제외)" 배경과 비교 -> 정상 스코어
        for i in range(n):
            others = np.delete(normal_stack, i, axis=0)
            mean = others.mean(axis=0)
            std = np.clip(others.std(axis=0), STD_FLOOR, None)
            z = (normal_imgs[i] - mean) / std
            s = topk_mean_abs(z, TOPK_RATIO)
            scores.append(s)
            labels.append(0)
            group_scores.append(s)
            group_labels.append(0)

        # anomaly는 전체 정상 프레임으로 만든 배경과 비교
        mean_full = normal_stack.mean(axis=0)
        std_full = np.clip(normal_stack.std(axis=0), STD_FLOOR, None)
        for p in anom_paths:
            z = (load_gray(p) - mean_full) / std_full
            s = topk_mean_abs(z, TOPK_RATIO)
            scores.append(s)
            labels.append(1)
            group_scores.append(s)
            group_labels.append(1)

        group_auc = None
        if len(np.unique(group_labels)) > 1:
            group_auc = roc_auc_score(group_labels, group_scores)
        per_group_rows.append(dict(key=key, n_normal=len(normal_paths), n_anom=len(anom_paths), auc=group_auc))

    scores_arr = np.array(scores)
    labels_arr = np.array(labels)

    print(f"(facility,camera) 그룹: 전체 {len(all_keys)}개 중 사용 {usable_keys}개 "
          f"(정상 {MIN_GROUP_FRAMES}장 미만이거나 anomaly 없어서 스킵 {skipped_keys}개)")
    if group_frame_counts:
        print(f"그룹별 정상 프레임 수: min={min(group_frame_counts)} "
              f"median={int(np.median(group_frame_counts))} max={max(group_frame_counts)}")
    print(f"평가 프레임: normal={int((labels_arr == 0).sum())} anomaly={int((labels_arr == 1).sum())}")

    if len(np.unique(labels_arr)) < 2:
        print("한쪽 클래스만 있어 AUC 계산 불가")
        return

    auc = roc_auc_score(labels_arr, scores_arr)
    print(f"\nAUC (전체 그룹 통합, 픽셀 배경모델, top{int(TOPK_RATIO * 100)}% |z| 평균) = {auc:.4f}")

    # 정상 프레임 수(=히스토리 깊이)가 가장 많은 그룹부터 정렬해서 그룹별 AUC 확인
    per_group_rows.sort(key=lambda r: r["n_normal"], reverse=True)
    print(f"\n=== 정상 프레임 수(히스토리) 기준 상위 15개 그룹의 개별 AUC ===")
    print(f"{'key':20s} {'정상':>4s} {'이상':>4s} {'AUC':>7s}")
    for row in per_group_rows[:15]:
        auc_str = f"{row['auc']:.4f}" if row['auc'] is not None else "N/A"
        print(f"{row['key']:20s} {row['n_normal']:4d} {row['n_anom']:4d} {auc_str:>7s}")


if __name__ == "__main__":
    main()
