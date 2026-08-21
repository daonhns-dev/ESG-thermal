# ESG 열수송관 열화상 이상탐지

## 프로젝트 배경

ESG 열수송관(district heating pipe) 설비를 대상으로, 열화상 이미지에서 **파이프(관) 자체의 이상 열 패턴**을 주변의 확산성 열 이상(diffuse thermal anomaly)과 구분해 탐지하는 프로젝트입니다.

열화상 이상탐지 모델(AE, EfficientAD)은 정상 패턴과 다른 영역을 모두 이상으로 잡아내는데, 실제 관심 대상인 "관 형태의 날카로운(sharp) 이상"과 조명·배경 등에서 오는 "뭉툭하고 퍼진(diffuse) 이상"을 구분하지 못하면 오탐이 많아집니다. `scripts/experiments/heat_pipe_shape_heuristic.py`가 이상 영역의 **가장자리 선명도(edge sharpness)** 와 **원형도(circularity)** 로 두 종류를 구분하는 형태 휴리스틱이며, `heat_pipe_shape_sweep.py`로 합성 데이터에서 이 구분이 유효한 범위를 검증합니다.

**현재 상태(2026-08-20)**: 실제 협력업체 데이터(차량 이동 촬영, `.att`/`.atg`/`.avi`)를 받아 위 가설을 실측 검증한 결과, 배관 매설 경로의 GPS/GIS 좌표 없이는 배관 위치 자체를 특정할 수 없어 신뢰성 있는 이상탐지가 어렵다는 결론에 도달, 좌표 확보 전까지 보류 중입니다. 자세한 경과와 재개 조건은 [`heat-pipe/docs/EXPERIMENT_SUMMARY.md`](heat-pipe/docs/EXPERIMENT_SUMMARY.md) 참고.

## 저장소 히스토리에 대한 참고

이 코드베이스는 원래 NCC(나프타 분해로) 열화상 이상탐지 프로젝트와 같은 저장소에서 브랜치로 진행되다가, 별도 고객사 프로젝트임이 명확해져 독립 저장소로 분리되었습니다. `models/`, `datasets/dataset.py`, `utils/`, 학습 스크립트(`train*.py`, `inference*.py`, `distill_pdn.py`, `grid_search_efficientad_params.py`, `split_val.py`) 등 기반 코드는 NCC 프로젝트와 기법을 공유하는 범용 AE/EfficientAD 학습 인프라라 유지했지만, NCC 자체 데이터(aircon/117 facility/hv_motor 등)에 종속된 실험 스크립트·config는 이후 정리 과정에서 삭제했습니다. 커밋 히스토리는 이 프로젝트에서 새로 작성된 파일(형태 휴리스틱)만 보존했고 나머지는 분리 시점의 스냅샷으로 가져왔습니다.

## 구조

```
heat-pipe/
├── models/          # AE, EfficientAD(+PDN) 모델 정의
├── datasets/
│   ├── dataset.py         # PyTorch 데이터셋 로더
│   └── att_atg_io.py      # 실제 협력업체 .att(열화상 raw)/.atg(GPS+타임스탬프) 리더
├── utils/
│   └── thermal_viz.py     # 컬러맵 변환 + 유니코드 경로 이미지 저장 (공용, 아래 여러 스크립트가 씀)
├── configs/          # 학습 설정 (AE / EfficientAD 기본형)
├── docs/
│   └── EXPERIMENT_SUMMARY.md   # 실제 데이터 검증 경과·결론 기록
├── scripts/
│   ├── train.py                       # AE 학습
│   ├── train_efficientad.py           # EfficientAD 학습
│   ├── inference*.py                  # 추론
│   ├── distill_pdn.py, grid_search_efficientad_params.py, split_val.py  # 학습 보조
│   ├── build_rgb_thermal_dataset.py   # .avi+.att+.atg -> RGB/열화상/GPS 페어 데이터셋
│   ├── render_thermal_video.py        # .att -> 컬러맵 mp4 (육안 확인용)
│   └── experiments/                   # 개별 분석·검증 스크립트
│       ├── heat_pipe_shape_heuristic.py    # 파이프 형태 vs 확산 이상 구분 휴리스틱 (합성 데이터)
│       ├── heat_pipe_shape_sweep.py        # 반경/강도 스윕 검증
│       ├── analyze_session.py              # 세션 내 후보 탐지(ROI z-score) + 프레임 간 추적(동행 차량 구분)
│       └── gps_tools.py                    # GPS 도구 3종: match(반복 통과 매칭) / review(bin별 비교 이미지) / kml(궤적 내보내기)
└── CNN/             # 합성 온도 이상 민감도 분석, 로컬 이상 맵 시각화 (z_score_map/compute_background를 analyze_session.py가 재사용)
```

## 빠른 시작

```bash
pip install -r heat-pipe/requirements.txt

# AE 학습
python heat-pipe/scripts/train.py --config heat-pipe/configs/config_ae.yaml

# EfficientAD 학습
python heat-pipe/scripts/train_efficientad.py --config heat-pipe/configs/config_efficientad.yaml

# 형태 휴리스틱 민감도 스윕
python heat-pipe/scripts/experiments/heat_pipe_shape_sweep.py

# 실측 데이터 파이프라인 (docs/EXPERIMENT_SUMMARY.md 참고)
python heat-pipe/scripts/build_rgb_thermal_dataset.py --dry-run
python heat-pipe/scripts/experiments/analyze_session.py --session <세션이름>
python heat-pipe/scripts/experiments/gps_tools.py match
```

세부 실험별 스크립트 설명은 [`heat-pipe/scripts/experiments/README.md`](heat-pipe/scripts/experiments/README.md) 참고.
