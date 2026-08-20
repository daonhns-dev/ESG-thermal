"""
117 산업시설 열화상 CCTV 데이터(K:/thermal_cctv_dataset) 재활용 전, aircon(§8-16)에서
발견한 "location(촬영 지점)이 라벨과 완전 분리" confound가 117에도 있는지 먼저 검증.

각 JSON 라벨의 metadata.facility_id / metadata.standard(설비 규격, 개체 식별에 가까움)를
"촬영 지점/개체" 키로 보고, 이 키가 normal/danger 상태와 얼마나 섞여 있는지 확인.
- 만약 facility_id(혹은 facility_id+standard) 단위로도 normal/danger가 완전 분리되어
  있다면, aircon과 동일한 개체-confound가 있다는 뜻 → CSV 고정 스케일 재렌더링을 해도
  "같은 개체의 정상/이상"을 비교 학습하는 게 아니라 "이 개체 vs 저 개체"를 배우게 됨.
- 반대로 같은 facility_id 안에 normal/danger가 섞여 있다면(=같은 설비의 정상 시점과
  이상 시점을 둘 다 촬영), 진짜 상태 변화 신호를 학습할 여지가 있다는 뜻.

JSON이 매우 많아(약 36만 개) 전수조사는 느릴 수 있어 --sample_rate로 서브샘플링 지원.

사용법:
  python scripts/experiments/analyze_117_facility_confound.py \
      --labels_dir "K:/thermal_cctv_dataset/labels" --sample_rate 1
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels_dir", default="K:/thermal_cctv_dataset/labels")
    ap.add_argument("--sample_rate", type=int, default=1, help="N개당 1개만 파싱(속도 조절). 1이면 전수조사.")
    args = ap.parse_args()
    root = Path(args.labels_dir)

    # category(대분류) -> facility_id -> {"normal": n, "danger": n, "standards": {}}
    by_category = defaultdict(lambda: defaultdict(lambda: {"normal": 0, "danger": 0, "standards": defaultdict(lambda: {"normal": 0, "danger": 0})}))

    n_total, n_parsed, n_err = 0, 0, 0
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        category = sub.parent.name if sub.parent != root else "?"
        for subsub in sorted(p for p in sub.iterdir() if p.is_dir()) if sub.is_dir() else []:
            equip = subsub.name
            key_prefix = f"{sub.name}/{equip}"
            files = sorted(subsub.glob("*.json"))
            for i, jf in enumerate(files):
                n_total += 1
                if args.sample_rate > 1 and i % args.sample_rate != 0:
                    continue
                try:
                    with open(jf, encoding="utf-8") as f:
                        d = json.load(f)
                    meta = d.get("metadata", {})
                    status = meta.get("status", "?")
                    facility_id = meta.get("facility_id", "?")
                    # annotations의 standard(설비 규격) 사용 — 개체 식별에 더 가까움
                    standards = set()
                    for ann in d.get("annotations", []):
                        std = ann.get("attributes", {}).get("standard")
                        if std:
                            standards.add(std)
                    n_parsed += 1
                except Exception:
                    n_err += 1
                    continue

                status_key = "danger" if "danger" in status else ("normal" if "normal" in status else status)
                entry = by_category[key_prefix][facility_id]
                if status_key in ("normal", "danger"):
                    entry[status_key] += 1
                for std in (standards or {"(unknown)"}):
                    if status_key in ("normal", "danger"):
                        entry["standards"][std][status_key] += 1

    print(f"총 JSON {n_total}개, 파싱 {n_parsed}개, 에러 {n_err}개 (sample_rate={args.sample_rate})")

    print("\n" + "=" * 90)
    print("  설비 카테고리 x facility_id 별 normal/danger 분포 (완전분리 여부 확인)")
    print("=" * 90)
    total_facilities = 0
    mixed_facilities = 0
    separated_facilities = 0
    for key_prefix in sorted(by_category.keys()):
        print(f"\n-- {key_prefix} --")
        for facility_id in sorted(by_category[key_prefix].keys()):
            entry = by_category[key_prefix][facility_id]
            n_normal, n_danger = entry["normal"], entry["danger"]
            total_facilities += 1
            if n_normal > 0 and n_danger > 0:
                mixed_facilities += 1
                tag = "MIXED (같은 개체에 정상+이상 둘 다 존재)"
            else:
                separated_facilities += 1
                tag = "완전분리"
            print(f"    facility_id={facility_id:6s} normal={n_normal:6d}  danger={n_danger:6d}   {tag}")
            # standard(세부 규격) 단위까지 내려가서 재확인
            for std, sub_entry in sorted(entry["standards"].items()):
                sn, sd = sub_entry["normal"], sub_entry["danger"]
                if sn > 0 and sd > 0:
                    print(f"        standard={std!r:40s} normal={sn:5d} danger={sd:5d}  MIXED")

    print("\n" + "=" * 90)
    print("  요약")
    print("=" * 90)
    print(f"  전체 facility_id 그룹 수     : {total_facilities}")
    print(f"  normal+danger 혼재(MIXED)   : {mixed_facilities}")
    print(f"  완전분리(SEPARATED)          : {separated_facilities}")
    if total_facilities:
        print(f"  혼재 비율                    : {mixed_facilities/total_facilities*100:.1f}%")
    print("=" * 90)
    if mixed_facilities == 0:
        print("\n→ 모든 facility_id가 normal 아니면 danger 하나로만 완전분리됨.")
        print("  aircon(§8-16)과 동일한 개체-confound 구조. CSV 고정 스케일 재렌더링을 해도")
        print("  '같은 설비의 정상↔이상 변화'가 아니라 '이 개체 vs 저 개체'를 학습할 위험이 큼.")
    else:
        print(f"\n→ 일부(또는 전체) facility_id에 normal/danger가 혼재됨 — 같은 설비의 상태")
        print("  변화를 학습할 여지가 있음. CSV 고정 스케일 재렌더링을 시도해볼 근거가 있음.")


if __name__ == "__main__":
    main()
