# CNN — 라벨 비의존 순수 온도 이상탐지 검증 (2026-07-16)

> **참고**: 아래 내용은 NCC 프로젝트에서 작성된 원래 배경이지만, 이 폴더의
> `z_score_map`/`compute_background` 함수는 현재 `scripts/experiments/detect_hotspot_candidates.py`
> (ESG 열수송관 실측 데이터 국소 열원 탐지)에서 그대로 import해서 쓰고 있어 삭제하지 않고 유지함.
> 실행 예시(K드라이브 의존)는 ESG 쪽에서는 안 돌아감 — 함수 재사용 목적으로만 참고.

이름은 "CNN"이지만 실제로는 **CNN(신경망) 학습이 없는 순수 통계 방법**이다.
당초 "CNN으로 돌아가서 이미지 분류를 시도해보자"는 아이디어에서 출발했으나,
논의 끝에 "온도 관점에서 이상탐지가 원리적으로 가능한가"라는 더 근본적인
질문을 라벨/CNN 둘 다 없이 검증하는 방향으로 바뀌었다(`EXPERIMENT_SUMMARY.md`
§8-19 참고).

**이 폴더 범위는 §8-19(117 hv_motor 온도 통계 검증) 스크립트로 한정.** 성격이
다른 실험(예: 다른 데이터셋 confound 체크, 실제 CNN 분류기 등)은 각자 별도
폴더에 둘 것 — 폴더명이 "CNN"이라고 아무거나 여기 넣지 말 것(2026-07-16,
실수로 무관한 스크립트를 넣었다가 `plant_safety/`로 재분리한 적 있음).

| 스크립트 | 목적 |
|----------|------|
| `temp_anomaly_synthetic_sensitivity.py` | 117 hv_motor normal CSV에 합성 hotspot(delta)을 주입해, 순수 통계(배경 추정 + robust z-score)만으로 몇 ℃부터 탐지되는지 민감도 곡선(AUC vs delta)을 뽑음. 배경 추정 방식(gaussian/median, `--bg_configs`)을 비교 가능. |
| `visualize_local_anomaly_map.py` | 위 스코어링 로직을 프레임 전체 픽셀맵으로 확장해 히트맵/탐지 오버레이로 시각화. `--mode synthetic`(정답 위치를 아는 합성 검증) / `--mode danger`(실제 danger 라벨과 비교) 두 모드. ROI 확대 crop으로 작은 hotspot도 정밀 확인 가능. |

## 핵심 결론 (§8-19)
- 순수 온도 신호만으로 국소 이상탐지는 원리적으로 가능(라벨/CNN 불필요).
- 단, 배경 추정 방식에 따라 민감도-특이도 트레이드오프가 큼 — 종합 지표(AUC)만 보고 방식을 고르면 함정에 빠질 수 있음(median 필터가 patch AUC는 1등이었으나 전체 맵에서는 오탐 폭증).
- 남은 한계: 단일 프레임 통계로는 "그 설비 특유의 정상 고온"과 "진짜 이상"을 구분 못 함 — 위치별 정상 기준선(historical baseline)이 필요.

## 실행
`thermal/image/`에서 실행 (K드라이브의 `thermal_cctv_dataset_v1`에 의존):
```
python CNN/temp_anomaly_synthetic_sensitivity.py --n_frames 300 --radius 15
python CNN/visualize_local_anomaly_map.py --mode synthetic --n 8 --delta 15
python CNN/visualize_local_anomaly_map.py --mode danger --n 8
```

결과는 `../results/temp_anomaly_sensitivity/`, `../results/local_anomaly_maps/`에 저장됨(로컬 산출물, git 추적 안 함).
