"""
새 데이터셋(산업단지 열화상) → train/val/test 구성. **세션 단위 + 날짜(배경) 매칭 분할**.

※ 기존 `prepare_thermal_data.py`(117 데이터, per-image JSON status, 전량 복사)와 구분:
   이 파일은 산업단지 데이터(원천이 '정상상황/이상상황' 폴더로 분리)를 대상으로,
   **날짜 매칭 + 세션 단위 8:1:1 분할 + subset** 을 한다.

⚠️ 왜 "날짜 매칭"인가 (단순 세션 무작위 분할의 함정):
    세션 단위 분할(같은 촬영 연속분은 한 split에만)은 프레임 암기(leakage)를 막는 데는
    맞지만, 촬영일(=물리적 배경)이 몇 개뿐인 데이터에서 무작위로 세션을 배정하면
    "test/normal이 train에 없는 낯선 배경, test/anomaly는 train과 겹치는 익숙한 배경"처럼
    날짜가 불균형하게 갈릴 수 있다. 그러면 AE/EfficientAD는 "이상 여부"가 아니라
    "배경이 낯익은지"를 학습해 AUC가 역전되는 현상이 생긴다(실측: aircon 데이터 AUC 0.26).
    → 실제 배포(고정 카메라, 항상 같은 배경)에서는 "새 배경 일반화"가 애초에 불필요한 능력.
      필요한 건 "같은 배경 안에서 정상과 이상을 구별"하는 것.
    → 그래서 val/test용 세션은 **정상·이상이 함께 존재하는 촬영일**에서만 뽑아,
      두 클래스가 같은 배경을 공유하게 한다(배경 confound 제거).

세션(그룹) 키 = 파일명에서 끝 '_숫자'(프레임번호) 제거.
날짜 키 = 세션 키에서 'YY.MM.DD' 패턴 추출.
  예: AIR_NOM_20.11.04_CIC_P1432-P1437_D3_000001 → 세션: ..._D3, 날짜: 20.11.04

분할 전략:
  1. 정상·이상 각각 세션을 (촬영일)별로 묶는다.
  2. "정상 세션과 이상 세션이 공존하는 날짜" 를 val/test 후보로 삼는다.
  3. 그 날짜들에서 정상 세션 일부 + 이상 세션 일부를 val/test에 배정(세션 단위, 누수 없음).
  4. 나머지 정상 세션(공존 날짜의 잔여분 + 이상이 없는 날짜 전부)은 train.

입력 모드:
  (A) --normal_dir <dir> --anomaly_dir <dir>   (정상/이상 폴더 분리 — 기본)
  (B) --src <dir>                              (섞임: 파일명 '_NOM_' 유무로 분류)

사용 예:
  python scripts/prepare_new_dataset.py \
    --normal_dir "K:/.../05_aou/normal" --anomaly_dir "K:/.../05_aou/anomaly" \
    --out data/AIR_thermal --max_normal 30000 --frame_stride 2 --dry_run
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import numpy as np

EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{2}")


def list_images(d: Path):
    return [p for p in sorted(d.rglob("*")) if p.is_file() and p.suffix.lower() in EXTS]


def group_key(p: Path) -> str:
    """세션 키 = 파일명에서 끝 '_숫자'(프레임번호) 제거."""
    return re.sub(r"_\d+$", "", p.stem)


def date_key(session_key: str) -> str:
    """세션 키에서 촬영일(YY.MM.DD) 추출. 못 찾으면 세션 키 그대로(안전 폴백)."""
    m = DATE_RE.search(session_key)
    return m.group(0) if m else session_key


def build_groups(files, stride: int) -> dict:
    g: dict[str, list] = {}
    for p in files:
        g.setdefault(group_key(p), []).append(p)
    return {k: sorted(v)[::stride] for k, v in g.items()}


def by_date(groups: dict) -> dict:
    d: dict[str, list] = {}
    for k in groups:
        d.setdefault(date_key(k), []).append(k)
    return d


def copy_all(files, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    for src in files:
        shutil.copy2(src, dst / src.name)
    return len(files)


def split_by_target(groups: dict, keys, targets: dict) -> dict:
    """세션(그룹)을 '결손비율이 가장 큰 split'에 하나씩 배정(세션 통째, bin-packing)."""
    bins = {n: [] for n in targets}
    for k in sorted(keys, key=lambda k: -len(groups[k])):
        name = max(targets, key=lambda n: (targets[n] - len(bins[n])) / max(targets[n], 1e-9))
        bins[name].extend(groups[k])
    return bins


def sess_count(files):
    return len({group_key(f) for f in files})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=str, default=None, help="정상/이상 섞인 단일 폴더 (_NOM_로 분류)")
    ap.add_argument("--normal_dir", type=str, default=None)
    ap.add_argument("--anomaly_dir", type=str, default=None)
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--train_ratio", type=float, default=0.8)
    ap.add_argument("--val_ratio", type=float, default=0.1, help="test = 1 - train - val")
    ap.add_argument("--max_normal", type=int, default=0, help="사용할 정상 프레임 상한(0=전부). 세션 단위로 잘림")
    ap.add_argument("--frame_stride", type=int, default=1, help="세션 내 프레임 솎기(중복 감소, 예: 2)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry_run", action="store_true")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)

    if args.normal_dir and args.anomaly_dir:
        normal = list_images(Path(args.normal_dir))
        anomaly = list_images(Path(args.anomaly_dir))
    elif args.src:
        allf = list_images(Path(args.src))
        normal = [p for p in allf if "_NOM_" in p.name]
        anomaly = [p for p in allf if "_NOM_" not in p.name]
    else:
        sys.exit("입력: (--normal_dir & --anomaly_dir) 또는 --src")

    ng = build_groups(normal, args.frame_stride)
    ag = build_groups(anomaly, args.frame_stride)
    print(f"원본: 정상 {len(normal):,}장 / {len(ng)} 세션,  이상 {len(anomaly):,}장 / {len(ag)} 세션")
    if not ng or not ag:
        sys.exit("정상 또는 이상 0 — 경로/분류 확인.")

    n_by_date = by_date(ng)
    a_by_date = by_date(ag)
    common_dates = sorted(set(n_by_date) & set(a_by_date))
    only_normal_dates = sorted(set(n_by_date) - set(a_by_date))
    print(f"촬영일: 정상+이상 공존 {len(common_dates)}일 {common_dates}")
    if only_normal_dates:
        print(f"        정상만 있는 날짜 {len(only_normal_dates)}일 {only_normal_dates} (전량 train행)")
    if not common_dates:
        sys.exit("정상·이상이 공존하는 촬영일이 없음 — 날짜 매칭 분할 불가.")

    # --- val/test 후보 세션: 공존 날짜에서만 (배경 confound 제거) ---
    common_n_sessions = [s for d in common_dates for s in n_by_date[d]]
    common_a_sessions = [s for d in common_dates for s in a_by_date[d]]
    train_only_n_sessions = [s for d in only_normal_dates for s in n_by_date[d]]

    total_common_n = sum(len(ng[k]) for k in common_n_sessions)
    te_ratio = max(0.0, 1.0 - args.train_ratio - args.val_ratio)
    # 공존 날짜의 정상 세션을 train/val/test로 분할(세션 단위)
    ckeys = list(rng.permutation(common_n_sessions))
    nb = split_by_target(ng, ckeys, {
        "train": total_common_n * args.train_ratio,
        "val": total_common_n * args.val_ratio,
        "test": total_common_n * te_ratio,
    })

    # train은 "공존 날짜의 train 몫" + "정상만 있는 날짜 전량" (+ max_normal 상한)
    train_n = nb["train"] + [f for s in train_only_n_sessions for f in ng[s]]
    if args.max_normal and len(train_n) > args.max_normal:
        rng.shuffle(train_n)
        train_n = train_n[:args.max_normal]
    val_n, test_n = nb["val"], nb["test"]

    # --- 이상: 반드시 val/test 정상과 같은 날짜의 세션에서만 배정 ---
    val_n_dates = {date_key(group_key(f)) for f in val_n}
    test_n_dates = {date_key(group_key(f)) for f in test_n}
    akeys = list(rng.permutation(common_a_sessions))
    val_a, test_a = [], []
    for k in akeys:
        d = date_key(k)
        if d in val_n_dates and len(val_a) < len(val_n):
            val_a.extend(ag[k])
        elif d in test_n_dates and len(test_a) < len(test_n):
            test_a.extend(ag[k])

    out = Path(args.out)
    s_tr, s_va, s_te = sess_count(train_n), sess_count(val_n), sess_count(test_n)
    sa_va, sa_te = sess_count(val_a), sess_count(test_a)
    print(f"\n계획 (세션 단위 + 날짜 매칭, 배경 confound 제거):")
    print(f"  train/normal   {len(train_n):>7,}장 / {s_tr} 세션")
    print(f"  val/normal     {len(val_n):>7,}장 / {s_va} 세션 (날짜 {sorted(val_n_dates)})   "
          f"val/anomaly  {len(val_a):>6,}장 / {sa_va} 세션")
    print(f"  test/normal    {len(test_n):>7,}장 / {s_te} 세션 (날짜 {sorted(test_n_dates)})   "
          f"test/anomaly {len(test_a):>6,}장 / {sa_te} 세션")
    print(f"  (정상 train:val:test ≈ {len(train_n)}:{len(val_n)}:{len(test_n)})")
    if not val_a or not test_a:
        print("  [경고] val 또는 test anomaly가 비었음 — 공존 날짜/세션 수가 부족할 수 있음.")
    if args.dry_run:
        print("\n[dry_run] 복사 안 함.")
        return

    copy_all(train_n, out / "train" / "normal")
    copy_all(val_n, out / "val" / "normal")
    copy_all(val_a, out / "val" / "anomaly")
    copy_all(test_n, out / "test" / "normal")
    copy_all(test_a, out / "test" / "anomaly")
    print(f"\n완료 → {out}/  (train/normal, val/{{normal,anomaly}}, test/{{normal,anomaly}})")
    print(f"→ config: train_dir='{out}/train', test_dir='{out}/test'  (val은 {out}/val)")


if __name__ == "__main__":
    main()
