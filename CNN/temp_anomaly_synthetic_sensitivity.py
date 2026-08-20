"""순수 '온도' 관점 이상탐지 민감도 검증 (라벨 비의존, CNN/이미지 미사용).

배경 (docs/EXPERIMENT_SUMMARY.md 참고):
  §8-7: 재구성/feature 기반(AE, EfficientAD) 이상 스코어는 온도보다 구조/엣지에 더 민감함.
  §8-18: 117 hv_motor의 bbox 내부 CSV 온도만으로 danger AUC는 0.55~0.60에 그침.
  §8-18-1: danger 라벨의 판정 근거가 데이터에 기록돼있지 않아, 위 결과가 "온도 신호가
           약해서"인지 "라벨이 애초에 온도 기준이 아니라서"인지 구분이 안 됨.

이 스크립트는 사람이 매긴 라벨(danger/normal)에 전혀 기대지 않고 다음을 직접 검증한다:
  "정상 열화상의 CSV 온도값만 가지고, 국소적으로 진짜 온도가 델타(delta)만큼 오른
   합성 이상을 얼마나 민감하게 탐지할 수 있는가?"

방법 (v2 — 국소(local) 패치 스코어, paired 비교):
  1. status=normal 로 라벨된 hv_motor CSV(픽셀별 실측 온도 그리드)만 모음.
  2. 각 프레임에서 큰 스케일 가우시안 블러로 '배경(기대) 온도 분포'를 추정하고,
     원본-배경 잔차(residual)를 구함. 배경은 항상 원본(주입 전) 그리드로부터만
     계산 -> 주입한 delta가 배경 추정에 다시 섞여 들어가는 걸 방지.
  3. 프레임마다 무작위 위치를 하나 고정하고, 그 자리에 반경 R짜리 원형 블롭으로
     +delta(℃)를 인위 주입. 스코어는 "그 지점(patch) 잔차 평균"을 프레임 전체
     잔차의 median/MAD로 정규화한 robust z-score.
     -> v1(프레임 전체 최댓값 기준)은 모터 몸체 등 원래 뜨거운 부위가 있으면
        거기에 스코어가 묻혀 델타가 작을 때 전혀 반응하지 않는 문제가 있었음.
        v2는 "그 지점이 자기 프레임 잔차 분포 대비 얼마나 튀는가"만 보므로,
        기존 열원 유무와 무관하게 국소 delta 자체의 탐지 가능성을 측정.
  4. 같은 프레임·같은 위치에서 주입 전(label=0) vs 주입 후(label=1) 스코어로
     AUC 계산 (paired). delta를 0~20℃까지 sweep.

결과: results/temp_anomaly_sensitivity/ 에 CSV(수치) + PNG(곡선) 저장.

사용법 (thermal/image/ 에서 실행):
    python CNN/temp_anomaly_synthetic_sensitivity.py --n_frames 150 --radius 15
"""
import argparse
import glob
import json
import os

import numpy as np
from scipy.ndimage import gaussian_filter, median_filter
from sklearn.metrics import roc_auc_score

LABEL_ROOT = r"K:\thermal_cctv_dataset_v1\labels\01_western_power\wp_01_hv_motor"
IMAGE_ROOT = r"K:\thermal_cctv_dataset_v1\images\01_western_power\wp_01_hv_motor"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "results", "temp_anomaly_sensitivity")


def find_normal_csv_paths(label_root, image_root, limit=None):
    paths = []
    for ann_path in glob.glob(os.path.join(label_root, "*.json")):
        try:
            with open(ann_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("metadata", {}).get("status") != "normal":
            continue
        csv_name = data.get("csv", {}).get("filename_csv")
        if not csv_name:
            continue
        csv_path = os.path.join(image_root, csv_name)
        if os.path.exists(csv_path):
            paths.append(csv_path)
        if limit and len(paths) >= limit:
            break
    return paths


def parse_temp_csv(path, skip_header_lines=5, allow_bad_rows=False):
    """앞 몇 줄 메타데이터를 건너뛰고 세미콜론 구분 온도 그리드 파싱."""
    rows = []
    skipped_lines = 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return None

    for line_no, line in enumerate(lines[skip_header_lines:], start=skip_header_lines + 1):
        line = line.strip().rstrip(";")
        if not line:
            continue
        try:
            vals = [float(v) for v in line.split(";") if v]
        except ValueError:
            if allow_bad_rows:
                skipped_lines += 1
                continue
            raise ValueError(f"CSV parse failed: {path} line {line_no} has non-numeric value")
        if vals:
            rows.append(vals)
    if skipped_lines:
        print(f"⚠️ {path}: 파싱 실패 행 {skipped_lines}개 스킵됨 (grid 높이가 원본보다 작을 수 있음)")
    if not rows:
        return None

    width = min(len(r) for r in rows)
    grid = np.array([r[:width] for r in rows], dtype=np.float32)
    return grid


def parse_bg_configs(tokens):
    """'gaussian:45' 'median:91' 같은 문자열 목록을 (method, param) 리스트로 변환."""
    configs = []
    for tok in tokens:
        method, param = tok.split(":")
        configs.append((method, float(param)))
    return configs


def bg_config_label(method, param):
    return f"{method}{int(param)}"


def compute_background(grid, method, param):
    """배경(기대 온도 분포) 추정. 두 방식 비교:
    - gaussian: 큰 sigma로 스무딩. 구현이 단순하지만 sigma가 설비 크기보다 작으면
      큰 열원의 가장자리가 배경으로 안 지워지고 잔차에 계속 남음.
    - median: 큰 커널의 median filter. 스파이크(작은 이상)에 덜 흔들리면서도
      edge/구조는 gaussian보다 덜 뭉개는 경향 -> 넓은 열원과 좁은 이상을 더 잘 분리할 것으로 기대."""
    if method == "gaussian":
        return gaussian_filter(grid, sigma=param)
    elif method == "median":
        size = int(param)
        if size % 2 == 0:
            size += 1
        return median_filter(grid, size=size)
    raise ValueError(f"unknown bg method: {method}")


def circular_mask(shape, cy, cx, radius):
    h, w = shape
    yy, xx = np.ogrid[:h, :w]
    return (yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2


def local_patch_score(residual, mask, ref_med, ref_mad):
    """residual 중 mask(주입/샘플 위치) 영역 평균을, '주입 전' 잔차 분포 기준
    robust z-score로 환산. 프레임 전체 최댓값이 아니라 '그 지점'만 보므로
    기존에 존재하는 다른 열원(모터 몸체 등)에 스코어가 휘둘리지 않음."""
    patch_mean = residual[mask].mean()
    return float((patch_mean - ref_med) / ref_mad)


def make_bump(shape, cy, cx, radius, delta):
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    dist2 = (yy - cy) ** 2 + (xx - cx) ** 2
    return delta * np.exp(-dist2 / (2 * (radius / 2) ** 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n_frames", type=int, default=150, help="사용할 normal 프레임 수")
    ap.add_argument("--radius", type=int, default=15, help="합성 hotspot 반경(px)")
    ap.add_argument("--bg_configs", type=str, nargs="+", default=["gaussian:45", "gaussian:120", "median:91"],
                     help="배경 추정 방식 비교 목록. 'method:param' 형식 (gaussian:sigma 또는 median:kernel_size)")
    ap.add_argument("--deltas", type=float, nargs="+", default=[0, 1, 2, 3, 4, 5, 7, 10, 15, 20])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--label_root", type=str, default=LABEL_ROOT,
                    help="라벨 JSON이 있는 루트 디렉터리")
    ap.add_argument("--image_root", type=str, default=IMAGE_ROOT,
                    help="CSV 온도 파일이 있는 루트 디렉터리")
    ap.add_argument("--out_dir", type=str, default=OUT_DIR,
                    help="결과 CSV/PNG를 저장할 디렉터리")
    ap.add_argument("--allow_bad_rows", action="store_true",
                    help="CSV 비정상 행을 중단 없이 스킵(기본값은 파싱 실패 시 즉시 예외 발생)")
    args = ap.parse_args()
    bg_configs = parse_bg_configs(args.bg_configs)

    label_root = os.path.abspath(os.path.expanduser(args.label_root))
    image_root = os.path.abspath(os.path.expanduser(args.image_root))
    out_dir = os.path.abspath(os.path.expanduser(args.out_dir))

    print("normal CSV 경로 수집 중...")
    csv_paths = find_normal_csv_paths(label_root, image_root, limit=args.n_frames)
    print(f"normal CSV {len(csv_paths)}개 발견 (요청 {args.n_frames}개)")

    rng = np.random.default_rng(args.seed)
    grids = []
    for p in csv_paths:
        g = parse_temp_csv(p, allow_bad_rows=args.allow_bad_rows)
        if g is not None:
            grids.append(g)
    print(f"파싱 성공 {len(grids)}개 프레임")

    if len(grids) < 10:
        print("프레임이 너무 적습니다. 라벨/이미지 경로를 확인하세요.")
        return

    for g in grids:
        h, w = g.shape
        if h <= 2 * args.radius or w <= 2 * args.radius:
            raise ValueError(f"frame too small: shape={g.shape}, radius={args.radius}")

    inject_sites = []
    for g in grids:
        h, w = g.shape
        cy = rng.integers(args.radius, h - args.radius)
        cx = rng.integers(args.radius, w - args.radius)
        inject_sites.append((cy, cx))

    all_results = []
    for method, param in bg_configs:
        label = bg_config_label(method, param)
        print(f"\n=== bg_config={label} ===")

        prepared = []
        for g, (cy, cx) in zip(grids, inject_sites):
            background = compute_background(g, method, param)
            residual = g - background
            med = np.median(residual)
            mad = np.median(np.abs(residual - med)) * 1.4826 + 1e-6
            mask = circular_mask(g.shape, cy, cx, args.radius)
            prepared.append(dict(residual=residual, med=med, mad=mad, mask=mask, cy=cy, cx=cx, shape=g.shape))

        normal_scores = [local_patch_score(p["residual"], p["mask"], p["med"], p["mad"]) for p in prepared]

        for delta in args.deltas:
            if delta == 0:
                injected_scores = normal_scores
            else:
                injected_scores = []
                for p in prepared:
                    bump = make_bump(p["shape"], p["cy"], p["cx"], args.radius, delta)
                    injected_residual = p["residual"] + bump  
                    injected_scores.append(local_patch_score(injected_residual, p["mask"], p["med"], p["mad"]))
            y = np.array([0] * len(normal_scores) + [1] * len(injected_scores))
            scores = np.array(normal_scores + injected_scores)
            auc = roc_auc_score(y, scores) if delta > 0 else 0.5
            all_results.append(dict(bg_config=label, delta=delta, auc=auc,
                                     normal_score_mean=float(np.mean(normal_scores)),
                                     injected_score_mean=float(np.mean(injected_scores))))
            print(f"delta=+{delta:>4.1f}C  AUC={auc:.4f}  "
                  f"normal_score_mean={np.mean(normal_scores):.2f}  "
                  f"injected_score_mean={np.mean(injected_scores):.2f}")

    os.makedirs(out_dir, exist_ok=True)
    import csv as csv_mod
    csv_out = os.path.join(out_dir, "sensitivity_curve.csv")
    if all_results:
        with open(csv_out, "w", newline="", encoding="utf-8") as f:
            writer = csv_mod.DictWriter(f, fieldnames=list(all_results[0].keys()))
            writer.writeheader()
            writer.writerows(all_results)
        print("\nsaved:", csv_out)
    else:
        print("저장할 결과가 없습니다.")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.figure(figsize=(7, 5))
        for method, param in bg_configs:
            label = bg_config_label(method, param)
            rows = [r for r in all_results if r["bg_config"] == label]
            plt.plot([r["delta"] for r in rows], [r["auc"] for r in rows], marker="o", label=label)
        plt.axhline(0.9, color="gray", linestyle="--", linewidth=1, label="AUC=0.9")
        plt.xlabel("injected delta (C)")
        plt.ylabel("detection AUC (normal vs injected)")
        plt.title(f"temperature-only anomaly sensitivity: bg method comparison "
                  f"(radius={args.radius}px, n={len(grids)})")
        plt.legend()
        plt.tight_layout()
        png_out = os.path.join(out_dir, "sensitivity_curve.png")
        plt.savefig(png_out, dpi=150)
        print("saved:", png_out)
    except Exception as e:
        print("plot 저장 실패(무시 가능):", e)


if __name__ == "__main__":
    main()
