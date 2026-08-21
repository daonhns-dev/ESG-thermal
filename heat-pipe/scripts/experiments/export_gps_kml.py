"""
각 세션의 GPS 궤적(metadata.json의 frames[].lat/lon)을 KML로 내보낸다.
Google Earth나 Google 내 지도(My Maps, google.com/mymaps)에 파일을 바로 업로드하면
실제 지도 위에 주행 경로가 선으로 그려져서, 어느 동네/도로를 지나갔는지 한눈에 보인다.

ESG 배관 지도가 도로명 없이 그려져있어서 텍스트로 대조하기 어려울 때, 반대 방향으로
"우리가 찍은 GPS가 실제로 어디인지"부터 지도에 띄워보고 ESG 배관 지도랑 눈으로
비교하는 용도. 세션마다 다른 색으로 구분한다.

사용법 (heat-pipe/ 에서 실행):
    python scripts/experiments/export_gps_kml.py
    -> dataset/gps_tracks.kml 생성, Google Earth/My Maps에 업로드
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_DATASET_ROOT = r"E:\열수송관 모니터링 데이터\dataset"

COLORS = [  # KML은 AABBGGRR 순서(알파+파랑+초록+빨강)
    "ff0000ff", "ff00a5ff", "ff00ffff", "ff00ff00",
    "ffff0000", "ffff00ff", "ff808080", "ff0080ff",
]

KML_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<name>열수송관 모니터링 GPS 궤적</name>
"""

KML_FOOTER = "</Document>\n</kml>\n"


def session_placemark(session: str, points: list, color: str) -> str:
    coords = " ".join(f"{lon},{lat},0" for lat, lon in points)
    style_id = f"style_{session}"
    return f"""
<Style id="{style_id}">
  <LineStyle><color>{color}</color><width>3</width></LineStyle>
</Style>
<Placemark>
  <name>{session}</name>
  <styleUrl>#{style_id}</styleUrl>
  <LineString>
    <tessellate>1</tessellate>
    <coordinates>{coords}</coordinates>
  </LineString>
</Placemark>
<Placemark>
  <name>{session} 시작</name>
  <styleUrl>#{style_id}</styleUrl>
  <Point><coordinates>{points[0][1]},{points[0][0]},0</coordinates></Point>
</Placemark>
"""


def main():
    parser = argparse.ArgumentParser(description="세션별 GPS 궤적을 KML로 내보내기 (Google Earth/My Maps용)")
    parser.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output", type=str, default=None, help="기본: <dataset-root>/gps_tracks.kml")
    parser.add_argument("--sessions", type=str, nargs="*", default=None, help="특정 세션만 (기본: 전체)")
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root)
    session_dirs = sorted(p for p in dataset_root.iterdir() if p.is_dir() and (p / "metadata.json").exists())
    if args.sessions:
        session_dirs = [p for p in session_dirs if p.name in args.sessions]

    body = []
    n_written = 0
    for i, sd in enumerate(session_dirs):
        meta = json.loads((sd / "metadata.json").read_text(encoding="utf-8"))
        points = [(f["lat"], f["lon"]) for f in meta.get("frames", []) if "lat" in f and "lon" in f]
        if len(points) < 2:
            continue
        # KML이 너무 무거워지지 않게 최대 300개 점으로 간략화
        if len(points) > 300:
            step = len(points) // 300
            points = points[::step]
        color = COLORS[i % len(COLORS)]
        body.append(session_placemark(sd.name, points, color))
        n_written += 1

    out_path = Path(args.output) if args.output else dataset_root / "gps_tracks.kml"
    out_path.write_text(KML_HEADER + "".join(body) + KML_FOOTER, encoding="utf-8")
    print(f"완료: {n_written}개 세션 궤적 -> {out_path}")
    print("Google Earth로 더블클릭해서 열거나, https://www.google.com/mymaps 에서 새 지도 만들고 '가져오기'로 업로드하면 됨")


if __name__ == "__main__":
    main()
