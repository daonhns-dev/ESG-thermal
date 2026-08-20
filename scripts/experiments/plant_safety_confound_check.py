"""'무인 플랜트 안전 감시를 위한 데이터'(AI-Hub dataSetSn=71677) confound 사전 점검.

배경: 117(§8-17)/aircon(§8-16)에서 배운 교훈 — 새 데이터를 실험에 쓰기 전에
"정상/이상이 같은 설비·같은 카메라에서 촬영됐는지"부터 확인해야 함. 안 그러면
촬영 지점 자체가 라벨과 분리되는 confound(§8-16)에 빠져 어떤 모델을 써도
무의미한 결과가 나올 수 있음.

이 스크립트는 라벨 JSON의 meta_information(event, camera_id, mode 등)과
object_information.facility.id 를 이용해:
  1. event(클래스)별 표본 수
  2. facility_id x event 교차표 -> 같은 설비가 여러 event(정상/이상)에 다 나오는지
  3. camera_id x event 교차표 -> 같은 카메라가 여러 event에 다 나오는지
  4. mode(rgb/thermal) 분포 -> 원하는 event에 실제 thermal 데이터가 있는지
를 출력한다. 아직 "장비과열" 폴더가 없는 샘플로도 동작 확인 가능 (다른 category로 테스트).

사용법 (아무 위치에서나 실행 가능, 절대경로 사용):
    python CNN/plant_safety_confound_check.py --root "C:\\Users\\USER\\Downloads\\Sample\\02.라벨링데이터\\facility-accident"
    python CNN/plant_safety_confound_check.py --root "<본다운로드 경로>\\02.라벨링데이터\\facility-accident\\overheat"
"""
import argparse
import glob
import json
import os
from collections import Counter, defaultdict


def is_video_level(json_path):
    """파일명이 상위 폴더명과 같으면(=== _NN 접미사 없음) 비디오 레벨 meta json."""
    stem = os.path.splitext(os.path.basename(json_path))[0]
    parent = os.path.basename(os.path.dirname(json_path))
    return stem == parent


def collect_video_records(root):
    """클립(폴더) 단위로 1개 레코드씩 수집.

    일부 클립(§ 2026-07-22 확인, over-heat 300개 중 58개)은 비디오 레벨 요약 json
    자체가 없음(원본 프레임 수가 20장이 아니라 더 적은 클립들 — mp4/요약json 둘 다
    누락, 프레임 json은 있음). meta_information은 프레임 json에도 동일하게 들어있어서
    (event/camera_id 등) 비디오 레벨 json이 없으면 프레임 json 아무거나로 대체해서
    읽음 — 그래야 이 클립들도 confound 체크에서 누락되지 않음.
    """
    folders = defaultdict(list)
    for jp in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        folders[os.path.dirname(jp)].append(jp)

    records = []
    for parent_dir, jsons in folders.items():
        # 비디오 레벨 json을 우선 시도하고, 없거나 파싱 실패하면 프레임 json으로 대체
        ordered = sorted(jsons, key=lambda jp: not is_video_level(jp))
        data = None
        for jp in ordered:
            try:
                data = json.load(open(jp, encoding="utf-8-sig"))
                break
            except Exception as e:
                print("skip (parse fail):", jp, e)
                continue
        if data is None:
            continue
        meta = data.get("meta_information", {})
        obj = data.get("object_information", {})
        facility = obj.get("facility", {})
        records.append(dict(
            path=parent_dir,
            category=meta.get("category"),
            event=meta.get("event"),
            mode=meta.get("mode"),
            camera_id=meta.get("camera_id"),
            facility_id=facility.get("id"),
            facility_name=facility.get("facility_name"),
        ))
    return records


def print_crosstab(records, key, title):
    print(f"\n--- {title} ---")
    table = defaultdict(Counter)
    for r in records:
        table[r[key]][r["event"]] += 1
    n_mixed = 0
    for k, counter in sorted(table.items(), key=lambda kv: -sum(kv[1].values())):
        events = dict(counter)
        mixed = len(events) > 1
        n_mixed += mixed
        flag = "MIXED" if mixed else "single-event"
        print(f"  {key}={k}: {events}  [{flag}]")
    total = len(table)
    print(f"  => {key} 총 {total}개 중 {n_mixed}개가 여러 event에 걸쳐 나타남"
          f" ({n_mixed}/{total} = {n_mixed/total*100:.0f}%)"
          if total else "  => 데이터 없음")
    if total and n_mixed == 0:
        print(f"  ⚠️ 전부 단일 event에서만 나타남 -> §8-16과 동일한 confound 위험"
              f" (정상/이상이 서로 다른 {key}에서만 촬영됐을 가능성)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=str, required=True,
                     help="라벨링데이터의 category(또는 그 하위 event) 폴더 경로")
    args = ap.parse_args()

    records = collect_video_records(args.root)
    print(f"비디오 레벨 라벨 {len(records)}개 발견 (root={args.root})")
    if not records:
        print("라벨을 못 찾았습니다. --root 경로를 확인하세요"
              " (02.라벨링데이터 하위, *.json 이 있는 category/event 폴더).")
        return

    print("\n--- event별 표본 수 ---")
    for event, cnt in Counter(r["event"] for r in records).most_common():
        print(f"  {event}: {cnt}")

    print("\n--- mode(rgb/thermal)별 표본 수 ---")
    for mode, cnt in Counter(r["mode"] for r in records).most_common():
        print(f"  {mode}: {cnt}")
    if "thermal" not in {r["mode"] for r in records}:
        print("  ⚠️ 이 category/event에는 thermal 데이터가 없어 보입니다.")

    print_crosstab(records, "facility_id", "facility_id x event 교차표 (§8-17 스타일)")
    print_crosstab(records, "camera_id", "camera_id x event 교차표 (§8-16 스타일)")

    print("\n요약: facility_id/camera_id가 event 간에 MIXED로 잘 섞여 있으면"
          " confound 위험이 낮고(117과 비슷), 전부 단일 event 전용이면"
          " aircon(§8-16)과 같은 함정일 가능성이 높습니다.")


if __name__ == "__main__":
    main()
