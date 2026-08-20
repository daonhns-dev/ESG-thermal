"""
test 폴더에서 일부를 val 폴더로 이동하여 validation set 구성.

사용법:
  python scripts/split_val.py                  # 기본: 20% validation
  python scripts/split_val.py --ratio 0.15     # 15% validation
  python scripts/split_val.py --dry_run        # 실제 이동 없이 확인만
"""

import argparse
import random
import shutil
from pathlib import Path


def split_val(data_dir: Path, val_ratio: float, seed: int, dry_run: bool) -> None:
    test_dir = data_dir / "test"
    val_dir = data_dir / "val"

    classes = ["normal", "anomaly"]
    for cls in classes:
        src_dir = test_dir / cls
        dst_dir = val_dir / cls

        files = sorted(src_dir.glob("*"))
        files = [f for f in files if f.is_file()]

        if not files:
            print(f"  [{cls}] Not found, skipping.")
            continue

        n_val = int(len(files) * val_ratio)
        random.seed(seed)
        val_files = random.sample(files, n_val)

        print(f"\n[{cls}]")
        print(f"  Total: {len(files)}")
        print(f"  val move: {n_val} ({val_ratio*100:.0f}%)")
        print(f"  test remaining: {len(files) - n_val}")

        if dry_run:
            print(f"  [DRY RUN] No actual move, skipping.")
            continue

        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in val_files:
            shutil.move(str(f), str(dst_dir / f.name))

        print(f"  완료: {dst_dir}")

    if not dry_run:
        print("\n=== 결과 ===")
        for split in ["test", "val"]:
            for cls in classes:
                d = data_dir / split / cls
                n = len(list(d.glob("*"))) if d.exists() else 0
                print(f"  data/{split}/{cls}: {n} files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="test → val 분리")
    parser.add_argument("--data_dir", type=str, default="data", help="데이터 루트 디렉토리")
    parser.add_argument("--ratio", type=float, default=0.2, help="validation 비율 (기본: 0.2)")
    parser.add_argument("--seed", type=int, default=42, help="랜덤 시드")
    parser.add_argument("--dry_run", action="store_true", help="실제 이동 없이 결과만 확인")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not (data_dir / "test").exists():
        print(f"Error: {data_dir / 'test'} folder not found.")
        exit(1)

    print(f"Val split: {args.ratio*100:.0f}% (seed={args.seed})")
    if args.dry_run:
        print("[DRY RUN mode — No actual file move]")

    split_val(data_dir, args.ratio, args.seed, args.dry_run)
