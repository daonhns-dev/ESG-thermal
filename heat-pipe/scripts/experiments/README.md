# experiments — ESG 열수송관 실험 스크립트

전부 `heat-pipe/`에서 실행: `python scripts/experiments/<name>.py ...`

경과와 결론은 [`../../docs/EXPERIMENT_SUMMARY.md`](../../docs/EXPERIMENT_SUMMARY.md) 참고.

## 형태 휴리스틱 (합성 데이터 검증용)

| 스크립트 | 목적 |
|----------|------|
| `heat_pipe_shape_heuristic.py` | 국소 열원의 edge sharpness/circularity 계산 (핵심 함수, 다른 스크립트에서 재사용) |
| `heat_pipe_shape_sweep.py` | 반경/강도(delta) 스윕으로 형태 구분이 유효한 범위 검증 |

## 실측 데이터(협력업체 `.att`/`.atg`/`.avi`) 파이프라인

`build_rgb_thermal_dataset.py`로 세션 데이터셋을 만든 뒤 순서대로:

| 스크립트 | 목적 | 입력 → 출력 |
|----------|------|------|
| `detect_hotspot_candidates.py` | 세션 하나의 도로면 ROI 내 국소 열원 후보 탐지 | `thermal/*.npy` → `candidates.csv` |
| `track_hotspot_candidates.py` | 프레임 간 후보 추적 — 동행 차량(다른 패턴) vs 고정 지면 이상(다가가며 커지다 하단 이탈) 구분 | `candidates.csv` → `tracks.csv` |
| `match_gps_passes.py` | 여러 세션(=여러 날짜 통과분)을 GPS 기준으로 묶어 반복 통과 비교 | 전체 세션 `metadata.json`+`candidates.csv`(+`tracks.csv`) → `gps_bin_timeline.csv` |
| `render_gps_bin_comparisons.py` | GPS bin별 세션 간 프레임을 이미지로 렌더링 (육안 검토용) | `gps_bin_timeline.csv` → `gps_bin_review/*.png` |
| `export_gps_kml.py` | 세션 GPS 궤적을 Google Earth/My Maps용 KML로 내보내기 | `metadata.json` → `gps_tracks.kml` |

현재 결론: 위 파이프라인으로 찾은 후보는 전부 차량 등 도로 위 다른 물체로 확인됨 — 배관 매설 경로 좌표 없이는 신뢰성 있는 검증이 어려움. 자세한 내용은 `EXPERIMENT_SUMMARY.md` 참고.
