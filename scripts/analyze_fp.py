"""
FP 분석 스크립트
- score 통계, near/far ratio
- 카메라/설비 ID별 FP 분포
- 시간대별 FP 분포
- T1-T4 수동 라벨 로드 (선택)
"""
import re
from pathlib import Path
import json
from collections import Counter

import numpy as np

# ── 설정 ──────────────────────────────────────────────────────────────────────
RESULTS_PATH = Path("results/fp_analysis/batch_results.json")
THRESHOLD = 0.012436
FAR_CUTOFF = THRESHOLD + 0.002

# T1-T4 수동 라벨 파일
# 형식 : {"파일명.jpg": "T1", "파일명.jpg": "T1+T2", ...} 없으면 T1-T4 분석은 건너뜀.
TYPE_LABEL_PATH = Path("results/fp_analysis/fp_type_labels.json")
# ─────────────────────────────────────────────────────────────────────────────

def parse_name(path_str: str) -> dict:
    name = re.split(r"[/\\]", path_str)[-1]
    m = re.match(r"^(\d+)\((\d+)\)\((\d+)\)\d+\.jpg$", name)
    if m:
        return {"name":name, "camera_id": m.group(1), "time": m.group(2)}
    return {"name": name, "camera_id": "unknown", "time": "unknown"}

def print_section(title: str):
    print(f"\n{'='*50}")
    print(f" {title}")
    print(f"{'='*52}")

# ── 데이터 로드 ───────────────────────────────────────────────────────────────
with RESULTS_PATH.open("r", encoding="utf-8") as f:
    results = json.load(f)
fp_list = [rst for rst in results if rst["label_dir"] == "normal" and rst["pred"]]
scores = np.array([rst["score"] for rst in fp_list])

# ── 1. 기본 통계 ──────────────────────────────────────────────────────────────
print_section("1. FP 기본 통계")
print(f"FP count: {len(fp_list)}")
print(f"Score mean: {scores.mean():.6f}")
print(f"Score std: {scores.std():.6f}")
print(f"Score min: {scores.min():.6f}")
print(f"Score max: {scores.max():.6f}")

# threshold 근처 / 높은 구간 개수 세기 (예: 0.0145 기준)
near = sum(1 for s in scores if s < FAR_CUTOFF)
far = sum(1 for s in scores if s >= FAR_CUTOFF)
print(f"\nThreshold 근처 (score < {FAR_CUTOFF:.4f}): {near}장 ({near/len(scores)*100:.1f}%)")
print(f"Score 높음  (score >= {FAR_CUTOFF:.4f}): {far}장 ({far/len(scores)*100:.1f}%)")

print("\nScore 상위 10장:")
for rst in sorted(fp_list, key=lambda x: x["score"], reverse=True)[:10]:
    info = parse_name(rst["path"])
    print(f"  {info['name']:<42} {rst['score']:.6f}")
 
 # ── 2. 카메라 ID별 분포 ───────────────────────────────────────────────────────
print_section("2. 카메라/설비 ID별 FP 수")
cam_counter  = Counter()
cam_scores   = {}
for r in fp_list:
    info = parse_name(r["path"])
    cid  = info["camera_id"]
    cam_counter[cid] += 1
    cam_scores.setdefault(cid, []).append(r["score"])
 
normal_cam = Counter()
for r in results:
    if r["label_dir"] == "normal":
        normal_cam[parse_name(r["path"])["camera_id"]] += 1
 
print(f"{'카메라 ID':<14} {'FP':>4} {'전체normal':>10} {'FP율':>7}  {'avg score':>10}")
print("-" * 52)
for cid, cnt in cam_counter.most_common():
    total = normal_cam.get(cid, 0)
    rate  = cnt / total * 100 if total else 0
    avg   = np.mean(cam_scores[cid])
    print(f"  {cid:<12} {cnt:>4} {total:>10} {rate:>6.1f}%  {avg:>10.6f}")
 
# ── 3. 시간대별 분포 ──────────────────────────────────────────────────────────
print_section("3. 시간대별 FP 수")
time_counter = Counter()
time_scores  = {}
for r in fp_list:
    info = parse_name(r["path"])
    t    = info["time"]
    time_counter[t] += 1
    time_scores.setdefault(t, []).append(r["score"])
 
print(f"{'시간대':>6} {'FP':>4}  {'avg score':>10}")
print("-" * 28)
for t, cnt in time_counter.most_common():
    avg = np.mean(time_scores[t])
    bar = "█" * cnt
    print(f"  {t:>4}  {cnt:>4}  {avg:>10.6f}  {bar}")
 
# ── 4. T1~T4 타입 분석 (라벨 파일이 있을 때만) ───────────────────────────────
if TYPE_LABEL_PATH.exists():
    print_section("4. FP 타입별 분포 (T1~T4)")
    with TYPE_LABEL_PATH.open("r", encoding="utf-8") as f:
        type_labels = json.load(f)   
 
    type_counter = Counter()
    unlabeled    = 0
    for r in fp_list:
        name  = parse_name(r["path"])["name"]
        label = type_labels.get(name)
        if label:
            for t in label.split("+"):
                type_counter[t.strip()] += 1
        else:
            unlabeled += 1
 
    total_labeled = len(fp_list) - unlabeled
    print(f"라벨링 완료: {total_labeled}장 / 미라벨: {unlabeled}장\n")
    for t in sorted(type_counter):
        cnt  = type_counter[t]
        bar  = "█" * cnt
        pct  = cnt / total_labeled * 100 if total_labeled else 0
        desc = {"T1": "핫스팟/반사", "T2": "엣지/케이블", "T3": "배경텍스처", "T4": "각도/프레이밍"}.get(t, "")
        print(f"  {t} ({desc:<12}) : {cnt:>3}장 ({pct:>5.1f}%)  {bar}")
else:
    print(f"\n[선택] T1~T4 라벨 파일 없음: {TYPE_LABEL_PATH}")
    print("  → 61장을 직접 분류 후 fp_type_labels.json 작성 시 타입별 통계가 출력됩니다.")
    print('  형식 예: {"0101011116(1042)(0236)0101.jpg": "T2+T4", ...}')
 