"""117(hv_motor) danger 라벨 샘플을 열화상+RGB 나란히 저장해서 육안 확인.

목적: RGB가 danger 판정에 쓸모 있는 시각 정보(부식/마모/형태 등)를
실제로 담고 있는지, 멀티모달 파이프라인을 짜기 전에 싸게 확인.

- bbox 좌표는 열화상 프레임(라벨 json의 width/height) 기준이므로,
  RGB 프레임 크기로 스케일링해 근사 정합한 크롭을 함께 보여줌
  (카메라가 달라 완벽한 정합은 아님 — 참고용).
- 결과: results/rgb_inspection/ 아래 {n_samples}장의 비교 이미지(PNG)
  [열화상 전체 | 열화상crop | RGB crop | RGB 전체]

사용법:
    python scripts/experiments/inspect_hv_motor_danger_rgb.py --n 12
    python scripts/experiments/inspect_hv_motor_danger_rgb.py --n 12 --status normal   # 정상도 같이 보고 싶을 때
"""
import argparse
import glob
import json
import os
import random

from PIL import Image, ImageDraw

LABEL_ROOT = r"K:\thermal_cctv_dataset_v1\labels\01_western_power\wp_01_hv_motor"
IMAGE_ROOT = r"K:\thermal_cctv_dataset_v1\images\01_western_power\wp_01_hv_motor"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "results", "rgb_inspection")


def load_matching_samples(status: str):
    samples = []
    for ann_path in glob.glob(os.path.join(LABEL_ROOT, "*.json")):
        try:
            data = json.load(open(ann_path, encoding="utf-8"))
        except Exception:
            continue
        img_info = data.get("image", {})
        thermal_name = img_info.get("filename")
        rgb_name = img_info.get("filename_rgb")
        if not thermal_name or not rgb_name:
            continue
        meta = data.get("metadata", {})
        tw, th = meta.get("width"), meta.get("height")
        for inst in data.get("annotations", []):
            attrs = inst.get("attributes", {})
            if attrs.get("status") != status:
                continue
            box = inst.get("data", {})
            samples.append(
                dict(
                    thermal_path=os.path.join(IMAGE_ROOT, thermal_name),
                    rgb_path=os.path.join(IMAGE_ROOT, rgb_name),
                    box=box,
                    tw=tw,
                    th=th,
                    facility=meta.get("facility", ""),
                    standard=attrs.get("standard", ""),
                )
            )
    return samples


def make_comparison(sample, pad_ratio=0.3):
    if not (os.path.exists(sample["thermal_path"]) and os.path.exists(sample["rgb_path"])):
        return None
    thermal = Image.open(sample["thermal_path"]).convert("RGB")
    rgb = Image.open(sample["rgb_path"]).convert("RGB")

    tw, th = sample["tw"] or thermal.width, sample["th"] or thermal.height
    box = sample["box"]
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]

    # thermal crop (약간 여유를 둠)
    px, py = w * pad_ratio, h * pad_ratio
    t_crop = thermal.crop((
        max(0, x - px), max(0, y - py),
        min(thermal.width, x + w + px), min(thermal.height, y + h + py),
    ))

    # RGB는 프레임 크기가 다르므로 비율로 근사 스케일링(완벽 정합 아님)
    sx, sy = rgb.width / tw, rgb.height / th
    rx, ry, rw, rh = x * sx, y * sy, w * sx, h * sy
    rpx, rpy = rw * pad_ratio, rh * pad_ratio
    r_crop = rgb.crop((
        max(0, rx - rpx), max(0, ry - rpy),
        min(rgb.width, rx + rw + rpx), min(rgb.height, ry + rh + rpy),
    ))

    # 원본에 bbox 표시
    thermal_boxed = thermal.copy()
    ImageDraw.Draw(thermal_boxed).rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=3)

    # 4분할 캔버스: 열화상 전체 | 열화상 crop | RGB crop | RGB 전체
    cell_h = 320
    def resize_h(im, h_target):
        ratio = h_target / im.height
        return im.resize((max(1, int(im.width * ratio)), h_target))

    imgs = [resize_h(thermal_boxed, cell_h), resize_h(t_crop, cell_h),
            resize_h(r_crop, cell_h), resize_h(rgb, cell_h)]
    total_w = sum(im.width for im in imgs) + 10 * (len(imgs) - 1)
    canvas = Image.new("RGB", (total_w, cell_h), (30, 30, 30))
    cx = 0
    for im in imgs:
        canvas.paste(im, (cx, 0))
        cx += im.width + 10
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--status", type=str, default="danger", choices=["danger", "normal"])
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    samples = load_matching_samples(args.status)
    print(f"status={args.status} 라벨 인스턴스 총 {len(samples)}개 발견")
    if not samples:
        print("샘플이 없습니다. LABEL_ROOT/IMAGE_ROOT 경로 및 K드라이브 마운트를 확인하세요.")
        return

    random.seed(args.seed)
    random.shuffle(samples)

    os.makedirs(OUT_DIR, exist_ok=True)
    saved = 0
    for i, s in enumerate(samples):
        if saved >= args.n:
            break
        canvas = make_comparison(s)
        if canvas is None:
            continue
        out_path = os.path.join(OUT_DIR, f"{args.status}_{saved:03d}_{s['standard']}.png")
        canvas.save(out_path)
        print("saved:", out_path)
        saved += 1

    print(f"\n총 {saved}장 저장 완료 -> {OUT_DIR}")
    print("각 이미지 구성: [열화상 전체(bbox 표시) | 열화상 crop | RGB crop(근사 정합) | RGB 전체]")


if __name__ == "__main__":
    main()
