"""Summarize verified local E0 demonstration runs for review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from aggregate_14_pairs import verify_run
from compare_selection import compare


def describe(path):
    counts = verify_run(path)
    result = json.loads((path / "analysis_result.json").read_text(encoding="utf-8"))
    frames = result["processing"]["frame_range"]
    candidates = result["candidates"]
    write_preview(path, candidates, frames["start"], frames["end_exclusive"])
    selection = compare(path, 50)
    audit_path = path / "raw_audit.json"
    return {
        "stem": result["monitoring_data_id"],
        "frames": frames,
        "elapsed": result["processing"]["elapsed_seconds"],
        "warnings": result["warnings"],
        "candidate_count": counts["returned_candidates"],
        "eligible_tracks": counts["eligible_tracks"],
        "selected_by_interval": selection["ten_interval_round_robin"]["by_tenth"],
        "top_candidates": candidates[:5],
        "audit": json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else None,
    }


def write_preview(path, candidates, start, end):
    first_by_section = {}
    for candidate in candidates:
        section = (candidate["representative_frame"] - start) * 10 // (end - start)
        first_by_section.setdefault(section, candidate)
    if not first_by_section:
        return False
    sample = candidates[0]["result_image"]
    columns = min(5, len(first_by_section))
    width, height = sample["width"], sample["height"] + 24
    rows = (len(first_by_section) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * height))
    labels = ImageDraw.Draw(sheet)
    for i, section in enumerate(sorted(first_by_section)):
        candidate = first_by_section[section]
        x, y = i % columns * width, i // columns * height
        with Image.open(path / candidate["result_image"]["path"]) as png:
            sheet.paste(png, (x, y + 24))
        labels.text((x + 6, y + 4),
                    f"section {section + 1} | rank {candidate['rank']} | frame {candidate['representative_frame']}",
                    fill="white")
    sheet.save(path / "interval-review.png")
    return True


def describe_decode_comparison(root, baseline_path):
    signed_path = root / f"{baseline_path.name}-signed"
    if not (signed_path / "experiment_summary.json").exists():
        return []
    before = json.loads((baseline_path / "analysis_result.json").read_text(encoding="utf-8"))
    after = json.loads((signed_path / "analysis_result.json").read_text(encoding="utf-8"))
    verify_run(signed_path)
    frames = after["processing"]["frame_range"]
    preview = write_preview(signed_path, after["candidates"], frames["start"], frames["end_exclusive"])
    temperature = "maximum"
    extreme_before = sum(c["temperature_c"][temperature] >= 600 for c in before["candidates"])
    extreme_after = sum(c["temperature_c"][temperature] >= 600 for c in after["candidates"])
    def location(candidate):
        box = candidate["candidate_bbox"]
        return candidate["representative_frame"], box["x"], box["y"], box["width"], box["height"]
    overlap = len({location(c) for c in before["candidates"]} & {location(c) for c in after["candidates"]})
    lines = [
        f"- 부호 있는 16비트 해석 비교에서는 600℃ 이상으로 표시된 반환 후보가 **{extreme_before}건 → {extreme_after}건**이었다. "
        f"상한 전 트랙은 **{before['detected_candidate_count']:,}건 → {after['detected_candidate_count']:,}건**이었다. "
        f"대표 프레임과 사각형이 같은 반환 후보는 **{overlap}건**이었다. "
        "이 결과는 온도 해석 가설을 확인한 실험이며 장비 사양 확인이 필요하다.",
        f"- [부호 있는 해석의 결과 JSON]({signed_path.relative_to(root).as_posix()}/analysis_result.json)",
    ]
    if preview:
        lines.append(f"- [부호 있는 해석의 10구간 예시]({signed_path.relative_to(root).as_posix()}/interval-review.png)")
    return lines


def write_report(root, paths):
    rows = [describe(path) for path in paths]
    lines = [
        "# 열수송관 실증 구간 열영상 분석",
        "",
        "2026-11-18 공유용. 외장하드의 실증 ATT·ATG를 현재 구현된 기준 알고리즘(E0)으로 로컬 GPU에서 전체 범위 분석했다. "
        "하단 가운데 ROI에서 주변 대비 열점을 찾아 프레임 사이에 연결하고, 구간별로 최대 50개 후보를 반환했다.",
        "",
        "| 입력 파일 | ATT 전체 | 분석 프레임 | ATG 레코드 | 적격 트랙 | 표시 후보 | 처리 시간 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        frames = row["frames"]
        lines.append(
            f"| {row['stem']} | {frames['total_att_frames']:,} | {frames['processed_frame_count']:,} | "
            f"{frames['total_atg_records']:,} | {row['eligible_tracks']:,} | {row['candidate_count']:,} | {row['elapsed']:.1f}초 |"
        )
    lines.extend([
        "",
        "후보는 현장 검토 대상이다. 이번 실증 파일과 매칭되는 현장 확인 위치가 제공되면 후보별로 비교할 수 있다. "
        "ATG 개수가 맞아도 프레임별 시각 정렬을 별도로 확인해야 한다. 아래 프레임 번호는 0부터 시작한다.",
        "",
    ])
    if ({row["stem"] for row in rows} == {"20261029_002254", "20261026_005923"}
            and (root / "20261029_002254/decode_comparison.png").exists()):
        lines.extend([
            "## 공유 순서",
            "",
            "1. [10월 26일 10구간 후보](20261026_005923/interval-review.png)에서 원본 열영상, 빨간 분석 범위, 초록 후보 위치를 보여준다. "
            "1위 후보(프레임 6,998)는 도로를 지나는 차량과 겹친다.",
            "2. [10월 29일 동일 프레임의 온도 해석 비교](20261029_002254/decode_comparison.png)에서 표시 문제가 난 원본 값을 설명한다. "
            "부호 있는 해석 결과는 장비 파일 형식 확인을 위한 비교 자료다.",
            "",
        ])
    for row, path in zip(rows, paths, strict=True):
        relative = path.relative_to(root).as_posix()
        lines.extend([f"## {row['stem']}", ""])
        if row["warnings"]:
            lines.extend(f"- 작업 경고: {warning['message']}" for warning in row["warnings"])
        lines.extend([
            f"- 10구간 후보 수: {', '.join(map(str, row['selected_by_interval']))}",
            f"- [전체 JSON]({relative}/analysis_result.json) · [후보표]({relative}/analysis_candidates.csv)",
        ])
        if (path / "interval-review.png").exists():
            lines.append(f"- [기준 결과 10구간 예시]({relative}/interval-review.png)")
        if row["audit"]:
            audit = row["audit"]
            lines.append(
                f"- 원본 온도 코드의 상위 비트가 켜진 프레임은 **{audit['high_bit_frames']:,}장**, "
                f"분석 ROI에 해당 픽셀이 포함된 프레임은 **{audit['high_bit_roi_frames']:,}장**이었다. "
                f"반환 후보 중 이 픽셀이 사각형에 들어간 후보는 **{len(audit['affected_candidate_boxes'])}건**이었다. "
                f"[원본 값 점검]({relative}/raw_audit.json)"
            )
            if (path / "decode_comparison.png").exists():
                item = audit["affected_candidate_boxes"][0]
                raw = item["unsigned_range"][0]
                lines.append(f"- 프레임 {item['frame']:,}의 코드 `{raw}`는 현재 해석으로 {raw / 100:.2f}℃, "
                             f"부호 있는 해석으로 {(raw - 65536) / 100:.2f}℃다. "
                             f"[같은 프레임의 비교 이미지]({relative}/decode_comparison.png)")
        lines.extend(describe_decode_comparison(root, path))
        lines.extend([
            "",
            "| 순위 | 대표 프레임 | 후보 최고 표시값 | 주변 대비 표시값 | 기준 결과 PNG |",
            "|---:|---:|---:|---:|---|",
        ])
        for candidate in row["top_candidates"]:
            temp = candidate["temperature_c"]
            png = candidate["result_image"]["path"]
            lines.append(
                f"| {candidate['rank']} | {candidate['representative_frame']:,} | "
                f"{temp['maximum']:.2f}℃ | {temp['delta_from_background']:.2f}℃ | "
                f"[이미지 보기]({relative}/{png}) |"
            )
        lines.append("")
    (root / "BRIEF_2026-11-18.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("stems", nargs="+")
    args = parser.parse_args()
    write_report(args.root, [args.root / stem for stem in args.stems])


if __name__ == "__main__":
    main()
