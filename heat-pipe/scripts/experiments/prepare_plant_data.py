"""
'무인 플랜트 안전 감시' 데이터의 장비과열 프레임을
train/normal, test/normal, test/anomaly 폴더 구조로 변환.

기존 prepare_new_dataset.py / prepare_thermal_data.py와 같은 결과물 포맷을 따름.

주의: 라벨(json)과 원천(png)이 별도 트리(예: label/TL/... vs raw/TS/...)로 나뉘어
있고 폴더명은 환경마다 다를 수 있음(01.원천데이터/02.라벨링데이터 또는 raw/label 등).
그래서 이름을 문자열 치환으로 추측하지 않고, --label_root/--image_root 두 경로를
각각 받아서 label_root 기준 상대경로를 image_root에 그대로 적용하는 방식으로 매칭함
(두 트리의 하위 상대 구조는 동일하다는 전제 — facility-accident/<event>/<mode>/<clip>/).
"""

import argparse
import glob
import json
import os
import random
import shutil

def collect_frame_records(label_root, image_root, mode_filter="thermal", progress=False):
    if progress:
        print("json 파일 목록 수집 중 (재귀 glob)...")
    json_paths = glob.glob(os.path.join(label_root, "**", "*.json"), recursive=True)
    total = len(json_paths)
    if progress:
        print(f"json 파일 {total}개 발견 -> 파싱 시작")

    records = []
    skipped_missing_png = 0
    for i, jp in enumerate(json_paths):
        if progress and total and i % 2000 == 0:
            print(f"  {i}/{total} 처리 중... (누적 매칭 {len(records)}개)")
        stem = os.path.splitext(os.path.basename(jp))[0]
        parent_dir = os.path.dirname(jp)
        parent = os.path.basename(parent_dir)
        if stem == parent:
            continue
        data = json.load(open(jp, encoding="utf-8-sig"))
        meta = data.get("meta_information", {})
        if meta.get("mode") != mode_filter:
            continue
        rel = os.path.relpath(parent_dir, label_root)
        png_path = os.path.join(image_root, rel, stem + ".png")
        if not os.path.exists(png_path):
            skipped_missing_png += 1
            continue
        records.append(dict(png=png_path, event=meta.get("event"), camera_id=meta.get("camera_id"),))

    if progress:
        print(f"파싱 완료: {total}/{total}")
    print(f"png 없어서 스킵된 프레임: {skipped_missing_png}개")
    return records


def clip_key(r):
    return os.path.basename(os.path.dirname(r["png"]))

def split_and_copy(records, normal_event, anomaly_events, out_dir, train_ratio=0.7, val_ratio=0.1, seed=42):
    """
    주의: out_dir 하위 폴더를 지우지 않고 copy2로 추가만 함. train_ratio/val_ratio를
    바꿔서 재실행하면 예전 분할 파일이 새 분할과 겹쳐 leakage가 생길 수 있음 —
    비율을 바꿀 땐 out_dir를 먼저 삭제하고 재실행할 것.
    """
    random.seed(seed)
    normals = [r for r in records if r["event"] == normal_event]
    if not normals:
        print(f"⚠️ normal_event={normal_event!r}에 해당하는 프레임이 0개입니다 — 이벤트명 오타 확인")
    anomalies = [r for r in records if r["event"] in anomaly_events]
    if not anomalies:
        print(f"⚠️ anomaly_events={anomaly_events!r}에 해당하는 프레임이 0개입니다 — 이벤트명 오타 확인")

    normal_clips = sorted({clip_key(r) for r in normals})
    random.shuffle(normal_clips)
    n = len(normal_clips)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    train_clips = set(normal_clips[:n_train])
    val_clips = set(normal_clips[n_train: n_train+n_val])

    train = [r for r in normals if clip_key(r) in train_clips]
    val = [r for r in normals if clip_key(r) in val_clips]
    test = [r for r in normals if clip_key(r) not in train_clips and clip_key(r) not in val_clips]

    for sub, items in [("train/normal", train), ("val/normal", val), ("test/normal", test), ("test/anomaly", anomalies)]:
        dst = os.path.join(out_dir, sub)
        os.makedirs(dst, exist_ok=True)
        for r in items:
            shutil.copy2(r["png"], os.path.join(dst, os.path.basename(r["png"])))

    print(f"train/normal={len(train)}  val/normal={len(val)}  "
          f"test/normal={len(test)}  test/anomaly={len(anomalies)}")
    

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label_root", required=True, help="라벨(json) 트리 경로 (예: .../label/TL/facility-accident)")
    ap.add_argument("--image_root", required=True, help="원천(png) 트리 경로 (예: .../raw/TS/facility-accident)")
    ap.add_argument("--normal_event", default="normal-over-heat")
    ap.add_argument("--anomaly_events", nargs="+", default=["over-heat"])
    ap.add_argument("--out_dir", default="data/plant_thermal")
    ap.add_argument("--mode", default="thermal")
    ap.add_argument("--progress", action="store_true", help="glob/파싱 진행 상황 출력(대용량 트리에서 멈춘 것처럼 보일 때)")
    args = ap.parse_args()

    records = collect_frame_records(args.label_root, args.image_root, mode_filter=args.mode, progress=args.progress)
    print(f"frame {len(records)}개 발견")
    split_and_copy(records, args.normal_event, args.anomaly_events, args.out_dir)


if __name__ == "__main__":
    main()