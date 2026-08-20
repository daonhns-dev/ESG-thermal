"""
Raw Thermal Image Data → data Folder Organization Script

Thermal images(JPG/PNG) → train/normal, test/normal (Phase 1)
RGB images → rgb/ (Phase 2)
CSV → csv/ (Phase 3)
JSON metadata matching for filename, filename_rgb, and status fields.

Usage:
    python scripts/prepare_thermal_data.py \
        --input "K:/산업시설 열화상 CCTV 데이터/1.서부발전/1.고압전동기" \
        --json-dir "K:/라벨링데이터/1.서부발전/1.고압전동기" \
        --output "data" \
        --split 0.8

    If --json-dir is not specified, searches for *.json files inside --input directory.
"""

import argparse
import json
import random
import shutil
from collections import Counter
from pathlib import Path


def find_json_files(dir_path: Path) -> list:
    """Collect JSON metadata files"""
    return list(dir_path.rglob("*.json"))


def find_csv_files(input_path: Path) -> list:
    """Collect CSV files"""
    return list(input_path.rglob("*.csv"))


def build_file_cache(input_path: Path, exts: list) -> dict:
    """Build stem → Path cache for locating images/files.
    - Only glob by extension (no full stat on everything)
    - First file wins on stem collision
    - Prints simple progress every N files
    """
    exts = {e.lower() for e in exts}
    cache = {}
    collisions = []
    count = 0

    print(f"[build_file_cache] Scanning images under: {input_path}")
    for ext in exts:
        for p in input_path.rglob(f"*{ext}"):
            count += 1
            if p.stem in cache and cache[p.stem] != p:
                collisions.append((p.stem, cache[p.stem], p))
            cache.setdefault(p.stem, p)
            if count % 1000 == 0:
                print(f"  - indexed {count} image files so far...")

    print(f"[build_file_cache] Done. Total indexed images: {count}, unique stems: {len(cache)}")
    if collisions:
        print(f"  [Warning] File name collision: {len(collisions)} files — using first path. Example stem: {collisions[0][0]!r}")
    return cache


def prepare_data(
    input_path: Path,
    output_path: Path,
    json_dir: Path = None,
    split: float = 0.8,
    seed: int = 42,
    fallback_csv: bool = False,
    skip_existing: bool = False,
):
    """Main data preparation: copy thermal images, RGB images, and CSV files.
    If skip_existing is True, skip copying when destination file already exists (for incremental runs).
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    json_dir = Path(json_dir) if json_dir else input_path

    train_normal = output_path / "train" / "normal"
    test_normal = output_path / "test" / "normal"
    test_anomaly = output_path / "test" / "anomaly"
    thermal_normal = output_path / "thermal" / "normal"
    thermal_anomaly = output_path / "thermal" / "anomaly"
    rgb_normal = output_path / "rgb" / "normal"
    rgb_anomaly = output_path / "rgb" / "anomaly"
    csv_dir = output_path / "csv"

    for d in [train_normal, test_normal, test_anomaly, thermal_normal, thermal_anomaly, rgb_normal, rgb_anomaly, csv_dir]:
        d.mkdir(parents=True, exist_ok=True)

    img_exts = [".jpg", ".jpeg", ".png", ".bmp"]
    img_cache = build_file_cache(input_path, img_exts)

    # 1. Collect samples from JSON metadata
    #    (JSON files from json_dir, images searched in input_path)
    #    If json_dir == input_path, there may be a mix of device sidecar .json files → continue if parsing fails
    json_files = find_json_files(json_dir)
    samples = []  # (thermal_path, rgb_path, status)
    json_no_filename = 0
    json_no_thermal = 0

    for json_path in json_files:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue

        img_info = meta.get("image", {})
        thermal_filename = img_info.get("filename", "")
        rgb_filename = img_info.get("filename_rgb", "")
        status = meta.get("metadata", {}).get("status", "normal")

        if not thermal_filename:
            json_no_filename += 1
            continue

        thermal_stem = Path(thermal_filename).stem
        thermal_path = img_cache.get(thermal_stem)
        if not thermal_path or not thermal_path.exists():
            json_no_thermal += 1
            continue

        rgb_path = None
        if rgb_filename:
            rgb_stem = Path(rgb_filename).stem
            rgb_path = img_cache.get(rgb_stem)

        samples.append((thermal_path, rgb_path, status))

    print(f"JSON: {len(json_files)} total → {len(samples)} matched")
    if json_no_filename:
        print(f"  - filename not found: {json_no_filename} files")
    if json_no_thermal:
        print(f"  - Thermal image not found (file missing in input directory): {json_no_thermal}")

    if not samples:
        print("No thermal images matched from JSON metadata.")
        if fallback_csv:
            _run_csv_fallback(input_path, output_path, split, seed)
        return

    # 2. train / test split (normal only, all anomaly samples to test)
    # 'danger' is treated as anomaly (same as 'anomaly')
    normal_samples = [(t, r, s) for t, r, s in samples if s == "normal"]
    anomaly_samples = [(t, r, s) for t, r, s in samples if s in ("anomaly", "danger")]
    other_status = [(t, r, s) for t, r, s in samples if s not in ("normal", "anomaly", "danger")]
    if other_status:
        counts = Counter(s for _, _, s in other_status)
        print(f"  [Warning] Skipping samples with non-'normal'/'anomaly'/'danger' status: {len(other_status)} — {dict(counts)}") 

    random.seed(seed)
    random.shuffle(normal_samples)
    split_idx = int(len(normal_samples) * split)
    train_samples = normal_samples[:split_idx]
    test_normal_samples = normal_samples[split_idx:]
    test_anomaly_samples = anomaly_samples

    # 3. Copy thermal images (Phase 1 + Phase 2 thermal/)
    def copy_thermal(src: Path, dest_dir: Path, name: str = None) -> bool:
        """Copy thermal image. Returns True if copied, False if skipped (e.g. skip_existing)."""
        name = name or src.name
        dest = dest_dir / name
        if src.suffix.lower() != Path(name).suffix.lower():
            dest = dest_dir / (Path(name).stem + src.suffix)
        if skip_existing and dest.exists():
            return False
        shutil.copy2(src, dest)
        return True

    thermal_count = 0
    for thermal_path, rgb_path, status in train_samples:
        thermal_count += copy_thermal(thermal_path, train_normal) + copy_thermal(thermal_path, thermal_normal)
    for thermal_path, rgb_path, status in test_normal_samples:
        thermal_count += copy_thermal(thermal_path, test_normal) + copy_thermal(thermal_path, thermal_normal)
    for thermal_path, rgb_path, status in test_anomaly_samples:
        thermal_count += copy_thermal(thermal_path, test_anomaly) + copy_thermal(thermal_path, thermal_anomaly)

    # 4. Copy RGB images (Phase 2)
    rgb_count = 0
    for thermal_path, rgb_path, status in train_samples + test_normal_samples + test_anomaly_samples:
        if rgb_path and rgb_path.exists():
            dest_dir = rgb_anomaly if status in ("anomaly", "danger") else rgb_normal
            dest = dest_dir / rgb_path.name
            if not (skip_existing and dest.exists()):
                shutil.copy2(rgb_path, dest)
                rgb_count += 1

    # 5. Copy original CSV files (Phase 3); parent directory name prefix for stem collisions
    csv_files = find_csv_files(input_path)
    csv_copied = 0
    for csv_path in csv_files:
        dest = csv_dir / csv_path.name
        if dest.exists() and dest.resolve() != csv_path.resolve():
            dest = csv_dir / f"{csv_path.parent.name}_{csv_path.name}"
        if skip_existing and dest.exists():
            continue
        shutil.copy2(csv_path, dest)
        csv_copied += 1

    if skip_existing:
        print(f"Thermal images: {thermal_count} new copies → train/normal, test/normal, thermal/")
        print(f"RGB images: {rgb_count} new copies → rgb/")
        print(f"CSV files: {csv_copied} new copies → csv/")
    else:
        print(f"Thermal images: {thermal_count} → train/normal, test/normal, thermal/")
        print(f"RGB images: {rgb_count} → rgb/")
        print(f"CSV files: {len(csv_files)} → csv/")
    print(f"Output: {output_path.absolute()}")


def _run_csv_fallback(input_path: Path, output_path: Path, split: float, seed: int):
    """Fallback: convert CSV to PNG when no thermal images are available"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from convert_thermal_csv_to_images import convert_csv_to_png

    train_normal = output_path / "train" / "normal"
    test_normal = output_path / "test" / "normal"
    thermal_normal = output_path / "thermal" / "normal"
    csv_dir = output_path / "csv"

    csv_files = list(input_path.rglob("*.csv"))
    if not csv_files:
        print("No CSV files found either.")
        return

    random.seed(seed)
    random.shuffle(csv_files)
    split_idx = int(len(csv_files) * split)
    for csv_path in csv_files[:split_idx]:
        out_path = train_normal / f"{csv_path.stem}.png"
        convert_csv_to_png(csv_path, out_path)
        if out_path.exists():
            shutil.copy2(out_path, thermal_normal / out_path.name)
    for csv_path in csv_files[split_idx:]:
        out_path = test_normal / f"{csv_path.stem}.png"
        convert_csv_to_png(csv_path, out_path)
        if out_path.exists():
            shutil.copy2(out_path, thermal_normal / out_path.name)
    for csv_path in csv_files:
        dest = csv_dir / csv_path.name
        if dest.exists() and dest.resolve() != csv_path.resolve():
            dest = csv_dir / f"{csv_path.parent.name}_{csv_path.name}"
        shutil.copy2(csv_path, dest)

    print("(fallback) Thermal images generated via CSV → PNG conversion.")


def main():
    parser = argparse.ArgumentParser(description="Thermal data preparation (copy thermal images)")
    parser.add_argument("--input", "-i", required=True, help="Source path for images/CSV (thermal, RGB, CSV)")
    parser.add_argument("--json-dir", "-j", default=None, help="JSON label directory (searches --input if not specified)")
    parser.add_argument("--output", "-o", default="data", help="Output path")
    parser.add_argument("--split", type=float, default=0.8, help="train split ratio (applied to normal samples only)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fallback-csv", action="store_true", help="Convert CSV -> PNG only when no thermal images are found")
    parser.add_argument("--skip-existing", action="store_true", help="Skip copying if destination already exists (incremental run; only new files are copied)")
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = Path(__file__).resolve().parent.parent / output

    json_dir = Path(args.json_dir) if args.json_dir else None
    prepare_data(args.input, output, json_dir=json_dir, split=args.split, seed=args.seed, fallback_csv=args.fallback_csv, skip_existing=args.skip_existing)
 

if __name__ == "__main__":
    main()
