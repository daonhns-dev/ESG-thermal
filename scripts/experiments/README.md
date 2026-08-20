# experiments — ROI · 열화상 특징 분석 스크립트

2026-07 세션에서 "EfficientAD가 열 이상을 국소화하는가"를 파고들며 만든 실험/분석/시각화
스크립트 모음. 결론은 `thermal/docs/EXPERIMENT_SUMMARY.md` §8-5~§8-8에 정리됨.

- 핵심 파이프라인(`train*.py`, `inference*.py`, `validate_efficientad_csv.py` 등)은 상위 `scripts/`에 유지.
- 모든 스크립트는 리포 루트가 아니라 **`thermal/image/`에서** 실행: `python scripts/experiments/<name>.py ...`
- 생성물(png/json)은 `results/predictions/`에 저장되며 `.gitignore` 대상(로컬 산출물).

---

## A. ROI 실험 (§8-5 ~ §8-7)

| 스크립트 | 목적 | 결론 |
|----------|------|------|
| `experiment_roi_mask.py` | 사후(post-hoc) 온도/기하 ROI 마스킹 + 재집계 | §8-5: 어떤 사후 마스킹도 풀프레임 못 넘음(음성) |
| `experiment_roi_train_ae.py` | AE 학습 시점 ROI(loss 마스킹) A/B (base vs roi) | §8-6: 국소화는 깨끗해지나 AUC 이득 없음 |
| `eval_efficientad_roi.py` | EfficientAD ROI 학습 모델의 **ROI-일치 평가**(ROI 재캘리 + ROI 스코어). `--checkpoint`로 base/roi 비교, 히트맵 저장 | §8-7: 3요소 일관 시 ROI 학습이 base +0.01~0.02 |

## B. 열화상 특징 분석 (§8-7)

| 스크립트 | 목적 | 결론 |
|----------|------|------|
| `diagnose_thermal_scaling.py` | 렌더 밝기↔온도 매핑이 프레임마다 다른지 → 절대온도 보존 여부 | 프레임별 auto-scale 확정(절대 °C 소실) |
| `analyze_edge_luminance_corr.py` | 이상맵 vs 온도/edge/luminance 픽셀 상관 | 정상=엣지 주도, 이상=온도 약하게 추종 |
| `analyze_roi_hot_vs_edge.py` | ROI 내부 상위 이상픽셀이 고온인지 엣지인지 | 온도 pctile 58 vs edge 76 → 엣지 추종 |
| `relative_hotspot_anomaly.py` | 밝기(=상대온도) local high-pass로 "상대 hotspot" 이상 정의 | 열은 잘 짚음(pctile 86)·탐지는 약함(0.61) |
| `viz_brightness_temp_corr.py` | 밝기 vs CSV 온도 산점도 → "이미지가 상대온도 보유" 확인 | r≈0.98 |

## C. 시각화 / 대조 (§8-7 ~ §8-8)

| 스크립트 | 목적 |
|----------|------|
| `viz_roi_vs_base.py` | 같은 입력에 base vs ROI 이상맵 오버레이 비교 |
| `viz_roi_vs_base_csv.py` | + CSV 실측 온도 열까지 나란히 (모델 반응 vs 실제 열구조) |
| `viz_normal_vs_anomaly_pairs.py` | 같은 설비의 정상 vs 이상 이미지 짝 비교(§8-8, 이상 정의 파악) |

---

### 스크립트 간 의존
`viz_roi_vs_base.py`, `viz_roi_vs_base_csv.py`, `analyze_roi_hot_vs_edge.py` 는
`eval_efficientad_roi.roi_recalibrate` 를 재사용한다. 상위 `scripts/`의
`inference_efficientad`, `train_efficientad`, `validate_efficientad_csv` 도 import 함.

## D. aircon / 117(hv_motor) confound 검증 (§8-11 ~ §8-18)

| 스크립트 | 목적 | 결론 |
|----------|------|------|
| `check_structural_complexity.py` | aircon AE 역전 원인 — 정상/이상 구조복잡도·밝기 비교 | §8-11: 정상이 구조 2배 복잡, 밝기 confound 원인 |
| `brightness_baseline_auc.py` | 학습 없이 픽셀 통계(밝기/엣지)만으로 탐지 AUC | §8-12: brightness_mean 단독 AUC 0.96~1.0 |
| `analyze_peak_brightness_confound.py` | brightness_mean 신호가 auto-scale 물리 현상인지 촬영조건 아티팩트인지 | §8-15: 미분리, 결론 보류 |
| `analyze_dataset_sessions.py`, `analyze_location_brightness.py` | 촬영 지점(location)별 정상/이상 분리·밝기 분석 | §8-16: location이 라벨과 완전 분리(confound 근본 원인) |
| `analyze_117_facility_confound.py` | 117 데이터에도 aircon과 같은 개체-confound 있는지 사전 검증 | §8-17: 117은 MIXED(정상/danger 같은 개체) 확인, confound 없음 |
| `render_hv_motor_fixed_scale.py` | hv_motor CSV로 전역 고정 스케일 재렌더링 | §8-18: confound 제거, 밝기 AUC 0.5~0.6대로 정상화 |
| `hv_motor_baseline_auc.py` | 고정 스케일 재렌더링 후 밝기/엣지 baseline 재확인 | §8-18: 사실상 무작위(confound 해소 확인) |
| `hv_motor_bbox_temp_auc.py` | bbox(설비 위치) 내부 온도 vs danger 라벨 AUC | §8-18: 약한 실신호(AUC 0.55~0.60)뿐, danger는 종합 상태 판정 |
| `inspect_hv_motor_danger_rgb.py` | 117 hv_motor danger bbox의 열화상+RGB 페어를 나란히 저장해 육안 검토(멀티모달 검토용, 2026-07-16) | 진행 중 — 결론 없음. `python scripts/experiments/inspect_hv_motor_danger_rgb.py --n 12` |

> D 스크립트들은 `K:\thermal_cctv_dataset_v1` (원본 산업시설 열화상) 또는
> `data/AIR_thermal` 로컬 경로에 의존하며, 리포 외부 경로라 재현 시 환경별 수정 필요.

## E. 무인 플랜트 안전 감시 데이터 검토 (AI-Hub dataSetSn=71677, 2026-07-16~)

117/aircon과 별개로 새로 검토 중인 대리 데이터. "장비과열"(정상/과열) 카테고리가
117의 "danger"(종합 상태 판정)보다 라벨이 명확할 것으로 기대되나, 방사온도 CSV는
없고 색상화 mp4/png만 제공됨(§8-19 이후 대화, `EXPERIMENT_SUMMARY.md`에는 아직 미기록).

| 스크립트 | 목적 |
|----------|------|
| `plant_safety_confound_check.py` | 라벨 JSON(`meta_information`, `object_information.facility`)으로 facility_id/camera_id가 event(정상/과열 등) 간에 섞여 있는지 확인 — §8-16/§8-17과 같은 confound 사전 점검. `python scripts/experiments/plant_safety_confound_check.py --root "<라벨 트리 경로>\facility-accident"`(하위 폴더명은 환경마다 다름 — `label/TL/...` 또는 `02.라벨링데이터/...` 등) |
| `prepare_plant_data.py` | confound 없음 확인되면 `train/normal`/`test/normal`/`test/anomaly` 구조로 변환(클립 단위 분할, 프레임 단위 leakage 방지), 기존 AE/EfficientAD 파이프라인에 연결. 라벨/원천이 별도 트리(폴더명이 환경마다 다름)라 `--label_root`/`--image_root` 둘 다 명시: `python scripts/experiments/prepare_plant_data.py --label_root "<라벨 트리>\facility-accident" --image_root "<원천 트리>\facility-accident" --normal_event normal-over-heat --anomaly_events over-heat --out_dir data/plant_thermal` |

> 2026-07-22 confound 체크 완료: facility_id/camera_id 모두 event 간 100% MIXED (§8-16 aircon형 confound 없음).
> `data/plant/`로 변환 완료(train/normal 3,318 / test/normal 876 / test/anomaly 5,406), 현재 `config_ae_plant.yaml`로 AE 학습 진행 중.

### 성공기준 (2026-07-23 사전등록, 결과 확인 전 작성)

학습 결과를 본 뒤 기준을 짜맞추는 걸 피하려고, AE/EfficientAD 결과 나오기 전에 먼저 적어둠.
육안 확인(§ facility 0082, 0087) 기준 신호 세기가 facility마다 들쭉날쭉했던 걸 감안한 기준.

| AUC | 판정 | 다음 액션 |
|---|---|---|
| 0.85 이상 | 실제 신호 잠정 인정 | brightness_baseline_auc.py(§8-12 스타일)로 온도 무관 신호(구도/밝기)만으로도 같은 AUC 나오는지 대조 — 그래야 "진짜 온도 신호"라고 말할 수 있음 |
| 0.55 ~ 0.7 | aircon/117급 애매한 신호 | confound 재의심 — brightness baseline부터 돌려서 픽셀 통계만으로 설명되는지 확인 |
| ~0.5 (무작위 수준) | 육안 패턴은 노이즈였다는 뜻 | 데이터/라벨 재검토, §8-19 순수 통계 탐지 결과와 재대조 |

> 참고: 이 데이터는 aircon/117과 달리 CSV(방사온도 원본)가 없어서, AUC가 높게 나와도
> "auto-scale 아티팩트인지 실제 온도 신호인지" 완전히 분리 못 할 수 있음(§8-15와 동일한 한계).
> brightness baseline 대조가 이를 배제하는 유일한 수단.
