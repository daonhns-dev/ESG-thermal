"""
117 고압전동기(wp_01_hv_motor) CSV → 전역 고정 스케일 PNG 재렌더링.

배경(§8-9, §8-16, §8-17): 열화상 카메라의 프레임별 auto-scale(각 프레임의
실측 min~max를 팔레트 전체로 재매핑)은 절대 온도 정보를 비가역적으로 없앤다.
CSV(픽셀별 실측 온도)가 있는 117 데이터는 이를 우회해, "정상 데이터의 최고온+
headroom"을 전역 고정 스케일로 삼아 재렌더링하면 절대적인 과열 정도가 밝기에
그대로 보존된다. §8-17에서 이 설비(facility_id/standard)는 aircon과 달리
같은 개체의 정상/이상이 함께 존재함(개체-confound 없음)을 확인했으므로 유효.

2단계로 사용:
  1) scan  — normal 라벨 CSV만 읽어 온도 분포 파악, 고정 스케일 후보 출력
  2) render — 정해진 min/max로 전체(normal+danger) CSV를 PNG로 재렌더링

사용법:
  python scripts/experiments/render_hv_motor_fixed_scale.py --mode scan \
      --csv_dir data/hv_motor_raw/csv --labels_dir data/hv_motor_raw/labels

  python scripts/experiments/render_hv_motor_fixed_scale.py --mode render \
      --csv_dir data/hv_motor_raw/csv --labels_dir data/hv_motor_raw/labels \
      --temp_min 15 --temp_max 60 --output_dir data/hv_motor_fixed_scale
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

N_COLS = 640  


def load_thermal_csv(csv_path: Path) -> np.ndarray:
    return np.loadtxt(csv_path, delimiter=";", skiprows=5, usecols=range(N_COLS))


def load_status(json_path: Path) -> str | None:
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)
    status = d.get("metadata", {}).get("status")
    if status is None:
        return None
    return "danger" if "danger" in status else ("normal" if "normal" in status else status)


def iter_pairs(csv_dir: Path, labels_dir: Path):
    for jf in sorted(labels_dir.glob("*.json")):
        cf = csv_dir / f"{jf.stem}.csv"
        if cf.exists():
            yield jf, cf


def cmd_scan(args):
    csv_dir, labels_dir = Path(args.csv_dir), Path(args.labels_dir)
    pairs = list(iter_pairs(csv_dir, labels_dir))
    print(f"라벨-CSV 쌍 {len(pairs)}개 발견")

    per_image_max, per_image_p999, per_image_min = [], [], []
    n_normal, n_danger, n_other, n_err = 0, 0, 0, 0

    for i, (jf, cf) in enumerate(pairs):
        status = load_status(jf)
        if status == "danger":
            n_danger += 1
            continue
        if status != "normal":
            n_other += 1
            continue
        n_normal += 1
        try:
            arr = load_thermal_csv(cf)
        except Exception as e:
            n_err += 1
            print(f"  [경고] {cf.name} 파싱 실패: {e}")
            continue
        per_image_max.append(float(arr.max()))
        per_image_p999.append(float(np.percentile(arr, 99.9)))
        per_image_min.append(float(arr.min()))
        if (i + 1) % 5000 == 0:
            print(f"  {i+1}/{len(pairs)} 처리 중...")

    per_image_max = np.array(per_image_max)
    per_image_p999 = np.array(per_image_p999)
    per_image_min = np.array(per_image_min)

    print(f"\nnormal={n_normal}  danger={n_danger}  other/unknown={n_other}  parse_err={n_err}")
    print("\n" + "=" * 70)
    print("  normal 데이터 온도 분포 (전역 고정 스케일 결정용)")
    print("=" * 70)
    print(f"  이미지별 최저온(min)     : mean={per_image_min.mean():.2f}  min={per_image_min.min():.2f}")
    print(f"  이미지별 최고온(max)     : mean={per_image_max.mean():.2f}  "
          f"median={np.median(per_image_max):.2f}  p95={np.percentile(per_image_max,95):.2f}  "
          f"p99={np.percentile(per_image_max,99):.2f}  max={per_image_max.max():.2f}")
    print(f"  이미지별 p99.9(강건 최고온): mean={per_image_p999.mean():.2f}  "
          f"p95={np.percentile(per_image_p999,95):.2f}  p99={np.percentile(per_image_p999,99):.2f}")

    robust_normal_max = float(np.percentile(per_image_max, 99))  # 극단 단일 프레임 노이즈 배제
    for headroom_pct in (0.10, 0.15, 0.20):
        scale_max = robust_normal_max * (1 + headroom_pct)
        print(f"\n  [후보] normal p99 최고온({robust_normal_max:.2f}) + headroom {headroom_pct*100:.0f}%"
              f" → temp_max = {scale_max:.2f}")
    print(f"\n  temp_min 후보: normal 이미지별 최저온 평균 근처 또는 소폭 낮은 라운드 값 권장")
    print(f"    (관측 범위 min={per_image_min.min():.2f} ~ mean={per_image_min.mean():.2f})")
    print("=" * 70)


def cmd_render(args):
    csv_dir, labels_dir = Path(args.csv_dir), Path(args.labels_dir)
    out_dir = Path(args.output_dir)
    temp_min, temp_max = args.temp_min, args.temp_max
    if temp_min is None or temp_max is None:
        raise ValueError("--mode render 사용 시 --temp_min, --temp_max 필수 (먼저 --mode scan으로 결정)")

    pairs = list(iter_pairs(csv_dir, labels_dir))
    print(f"라벨-CSV 쌍 {len(pairs)}개 발견 — 고정 스케일 [{temp_min}, {temp_max}]로 렌더링")

    for status_dir in ("normal", "danger"):
        (out_dir / status_dir).mkdir(parents=True, exist_ok=True)

    temp_range = temp_max - temp_min
    n_ok, n_skip, n_sat = 0, 0, 0
    for i, (jf, cf) in enumerate(pairs):
        status = load_status(jf)
        if status not in ("normal", "danger"):
            n_skip += 1
            continue
        try:
            arr = load_thermal_csv(cf)
        except Exception as e:
            n_skip += 1
            print(f"  [경고] {cf.name} 파싱 실패: {e}")
            continue

        if arr.max() > temp_max:
            n_sat += 1  

        normalized = np.clip((arr - temp_min) / temp_range, 0, 1)
        img_array = (normalized * 255).astype(np.uint8)
        img = Image.fromarray(img_array, mode="L")
        img.save(out_dir / status / f"{cf.stem}.png")
        n_ok += 1
        if (i + 1) % 5000 == 0:
            print(f"  {i+1}/{len(pairs)} 렌더링 중... (포화 {n_sat}건)")

    print(f"\n완료: {n_ok}개 렌더링, {n_skip}개 스킵, 상한 포화 {n_sat}개"
          f" ({n_sat/max(n_ok,1)*100:.1f}%)")
    print(f"출력: {out_dir.absolute()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["scan", "render"], required=True)
    ap.add_argument("--csv_dir", default="data/hv_motor_raw/csv")
    ap.add_argument("--labels_dir", default="data/hv_motor_raw/labels")
    ap.add_argument("--output_dir", default="data/hv_motor_fixed_scale")
    ap.add_argument("--temp_min", type=float, default=None)
    ap.add_argument("--temp_max", type=float, default=None)
    args = ap.parse_args()

    if args.mode == "scan":
        cmd_scan(args)
    else:
        cmd_render(args)


if __name__ == "__main__":
    main()
