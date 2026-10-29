# 열수송관 이상징후 알고리즘 현황 점검

- 점검일: 2026-10-29
- 목적: 돌봄휴가 이전 실험의 재현 가능성 확인과 기술실증용 알고리즘 v0 선정 준비

## 1. 결론

- 제품 흐름의 메인 후보는 `re_testbed`이다.
- `scripts/experiments/road_thermal`은 재방문 정합의 성립성을 검증하는 연구 실험이며 제품 메인 경로가 아니다.
- Python 코드 전체는 문법 컴파일을 통과했다.
- `road_thermal` 테스트 19개와 새 `re_testbed` 테스트 3개, 총 22개가 통과했다.
- 합성 열화상 10프레임을 사용한 `single_session_review` 스모크 테스트에서 후보 10건이 하나의 트랙으로 정상 병합됐다.
- 원본 데이터 경로 `E:\heatpipe\dataset`을 다시 연결한 뒤 기존 세 구간을 재실행했다.
- 세 구간의 알고리즘별 후보 수, 트랙 수, 최장 트랙, 최고 점수, 지속성 점수, 품질 점수가 기존 결과와 모두 정확히 일치했다.
- 재실행 결과에서도 551개 알고리즘 트랙이 384개 사례로 병합됐고, 기존과 동일한 50개 라벨 대상이 선택됐다.

## 2. 확인한 실행환경

```text
Python 3.11.9
NumPy 2.4.6
SciPy 1.17.1
OpenCV 5.0.0
Pandas 3.0.5
pytest 9.1.1
```

프로젝트 Python:

```text
C:\Users\USER\PycharmProjects\PythonProject\esg\heat\Scripts\python.exe
```

스크립트와 테스트는 `heat-pipe`를 현재 작업 디렉터리로 두고 실행해야 한다. 저장소 최상위에서 실행하면 `scripts` 패키지 경로를 찾지 못한다.

## 3. 검증 결과

### 코드 컴파일

```powershell
& '..\heat\Scripts\python.exe' -m compileall -q re_testbed scripts datasets utils
```

결과: 통과.

### road_thermal 테스트

```powershell
& '..\heat\Scripts\python.exe' -m pytest scripts\experiments\road_thermal -q
```

결과: `19 passed`.

### re_testbed 스모크 테스트

`tests/test_re_testbed_smoke.py`에 다음 검증을 추가했다.

- 네 점수맵이 합성 열점에서 유한한 스코어맵을 생성하는지 확인
- 세션 리뷰가 프레임 후보, 트랙, 요약 JSON을 생성하는지 확인

## 4. 기존 결과물

`results/re_testbed`에는 총 66개 파일이 남아 있다.

```text
JSON 19개
CSV 34개
PNG 13개
```

주요 세션과 범위:

- `20260813_140209`, frames 21120~21160, grid ROI 4/1
- `20260813_140209`, frames 21200~21280, grid ROI 4/1
- `20260814_131222`, frames 21120~21160, grid ROI 4/1

기존 라벨 표본 50건:

```text
vehicle 16
road_paint 23
road_surface_hotspot 11
```

알고리즘별 라벨 표본 내 결과:

| 알고리즘 | 표본 포함 건수 | road_surface_hotspot | 오탐 | 표본 내 목표 비율 |
|---|---:|---:|---:|---:|
| robust_zscore | 8 | 0 | 8 | 0.000 |
| top_hat_zscore | 23 | 4 | 19 | 0.174 |
| multiscale_top_hat | 35 | 8 | 27 | 0.229 |
| percentile_contrast | 24 | 6 | 18 | 0.250 |

이 표본은 의도적으로 다양하게 뽑았으므로 모집단 탐지율로 사용하지 않는다.

### 실제 데이터 재현 결과

재현 출력 위치:

```text
results/re_testbed/repro_20261029/
```

| 세션·프레임 | 비교 결과 |
|---|---|
| `20260813_140209`, 21120~21160 | 기존 알고리즘 요약과 정확히 일치 |
| `20260813_140209`, 21200~21280 | 기존 알고리즘 요약과 정확히 일치 |
| `20260814_131222`, 21120~21160 | 기존 알고리즘 요약과 정확히 일치 |

후보 병합·표본 재현:

```text
입력 알고리즘 트랙 551개
병합 사례 384개
선택 라벨 대상 50개
기존 대비 병합 필드 차이 0개
기존 대비 선택 case_id 차이 0개
```

### 후보 생성 알고리즘 조합 점검

기존 라벨 50건에서 알고리즘 존재 여부를 다시 집계했다.

| 후보 생성 조합 | 열이상 11건 중 포함 | 오탐 39건 중 포함 |
|---|---:|---:|
| robust_zscore | 0 | 8 |
| top_hat_zscore | 4 | 19 |
| multiscale_top_hat | 8 | 27 |
| percentile_contrast | 6 | 18 |
| multiscale + percentile | 11 | 36 |
| top_hat + multiscale + percentile | 11 | 39 |
| 전체 네 방법 | 11 | 39 |

첫 번째 표본 안에서는 `multiscale_top_hat + percentile_contrast` 조합이 열이상 라벨 11건을 모두 포함했다. 이 결론은 두 번째 라벨 큐를 추가한 뒤 아래와 같이 갱신됐다.

단, 이 결과는 다양성 표본 50건에만 해당한다. `multiscale + percentile`도 오탐 39건 중 36건을 포함하므로 후보 생성만으로는 부족하며, 경계 접촉·차량·도로 도색에 대한 재순위화가 반드시 필요하다.

추가 관찰:

- 열이상 라벨의 평균 최고 품질점수는 12.29였다.
- 도로 도색은 32.15, 차량은 20.09였다.
- 현재 품질점수의 높은 값이 실제 검토 우선순위를 의미하지 않는다.
- 기존 지속성 중심 품질점수는 차량과 도로 도색을 과도하게 올릴 수 있으므로 기술실증용 최종 순위점수로 그대로 사용하지 않는다.

### 기존 라벨 기반 재순위화 예비 점검

기존 50건에 대해 class weight를 적용한 로지스틱 회귀를 5-fold 교차검증했다. 이 점검은 구현 후보의 성립성 확인용이며 성능 근거로 사용하지 않는다.

```text
ROC-AUC 0.690
Average Precision 0.334
열이상 recall 0.909
열이상 precision 0.385
```

목표 재현율은 높지만 정밀도가 낮고 표본이 50건뿐이므로 재순위화 모델을 고정하기에 부족하다.

### 추가 라벨 큐

기존 50개 `case_id`를 제외하는 기능을 `label_candidate_export.py`에 추가했다. 두 번째 50건은 다음 위치에 생성했다.

```text
results/re_testbed/repro_20261029/label_candidates_round2/
```

구성:

```text
label_candidates.csv
label_candidates_panel.png
label_schema.json
merged_cases.csv
```

두 번째 큐는 기존 라벨 50건과 중복되지 않으며, 남은 334개 사례에서 50개를 선택했다.

### 두 번째 라벨 및 100건 통합 결과

두 번째 큐 50건의 사람 라벨이 완료됐다.

```text
road_surface_hotspot 28
vehicle 16
road_paint 3
unknown 3
```

1·2차 통합 100건:

```text
road_surface_hotspot 39
vehicle 32
road_paint 26
unknown 3
```

통합 파일과 공식 요약:

```text
results/re_testbed/repro_20261029/labels_combined/
```

| 알고리즘 | 포함 사례 | 열이상 | 오탐 | unknown | 표본 내 열이상 비율 |
|---|---:|---:|---:|---:|---:|
| robust_zscore | 16 | 5 | 9 | 2 | 0.312 |
| top_hat_zscore | 38 | 12 | 26 | 0 | 0.316 |
| multiscale_top_hat | 65 | 23 | 41 | 1 | 0.354 |
| percentile_contrast | 44 | 21 | 22 | 1 | 0.477 |

후보 생성 조합 재점검:

```text
multiscale + percentile: 열이상 37/39, 오탐 53/58
robust + multiscale + percentile: 열이상 39/39, 오탐 55/58
네 알고리즘 전체: 열이상 39/39, 오탐 58/58
```

두 번째 큐에서 `robust_zscore` 단독으로만 잡힌 열이상 라벨 2건이 추가됐다. 따라서 기술실증 v0의 후보 생성기는 `robust_zscore + multiscale_top_hat + percentile_contrast` 세 방법의 합집합을 우선 사용한다. `top_hat_zscore`는 이 100건에서 추가 열이상 없이 오탐 3건만 더해 기본 후보 생성기에서 제외한다.

100건 중 unknown을 제외한 97건으로 같은 로지스틱 회귀를 다시 교차검증한 결과:

```text
ROC-AUC 0.633
Average Precision 0.504
열이상 recall 0.718
열이상 precision 0.475
```

표본을 늘려도 현재 사례 특징만으로는 안정적인 재순위화가 어렵다. 기술실증 v0는 확정 판정 모델이 아니라 세 방법의 후보 합집합과 설명 가능한 점수·메타데이터를 제공하는 검토지원 방식으로 유지한다.

## 5. 현재 코드 경로

### 메인 제품 후보

- `re_testbed/single_session_review.py`
  - 한 세션의 선택 ROI를 분석한다.
  - 현재 기본 검출은 robust z-score 기반이다.
  - `frame_candidates.csv`, `tracks.csv`, `summary.json`을 생성한다.
- `re_testbed/algorithm_track_compare.py`
  - 네 점수맵을 같은 구간에서 비교한다.
  - 알고리즘별 트랙과 품질점수를 생성한다.
- `re_testbed/label_candidate_export.py`
  - 알고리즘 간 중복 트랙을 사례 단위로 병합하고 라벨 대상을 추출한다.

### 공용 구현

- `datasets/att_atg_io.py`: ATT·ATG 파서
- `utils/thermal_anomaly.py`: 배경 추정, robust z-score, 형태 특징
- `utils/thermal_tracks.py`: 후보 트래킹
- `re_testbed/algorithms.py`: 네 점수맵
- `re_testbed/candidate.py`: 연결요소 후보 추출
- `re_testbed/score_and_rank.py`: 후보 순위점수

### 연구 경로

- `scripts/experiments/road_thermal`
  - 원본 품질검사와 RGB–열화상 동기화 검증
  - 정합·합성 주입·재방문 변화 비교는 아직 완료되지 않음
  - 운영 API 경로에 직접 포함하지 않음

## 6. 확인된 불일치와 미결정 사항

### ROI

- 현재 코드: 하단 도로 영역을 3개 또는 4개의 세로 ROI로 분할
- 외부 화면 요구: 2행×3열 격자
- 분석 ROI와 화면 표시 격자를 분리할지 결정해야 한다.

### 메인 알고리즘

- `single_session_review`는 robust z-score 단일 흐름이다.
- 기존 라벨에서는 robust z-score가 `road_surface_hotspot`을 포함하지 못했다.
- 기술실증용 v0는 다중 알고리즘 후보 생성 또는 재순위화 방식을 재검증한 후 고정해야 한다.

### 출력

- 현재 세션 리뷰는 CSV와 요약 JSON을 생성하지만 ATG 시각·GPS와 결과 PNG를 최종 후보 스키마에 결합하지 않는다.
- INT-001 연동 전 대표 프레임, GPS, 온도차, ROI, PNG 경로를 포함하는 단일 결과 스키마가 필요하다.

### 원본 데이터

- 기존 결과가 참조하는 `E:\heatpipe\dataset`을 다시 연결했다.
- `20260813_140209`은 열화상 25,855프레임을 포함한다.
- `20260814_131222`은 열화상 21,731프레임을 포함한다.
- 두 세션의 기존 검증 범위가 모두 존재하며 재실행에 성공했다.

## 7. 다음 작업

1. ~~실제 데이터 위치를 복구하거나 새 경로를 확인한다.~~ 완료
2. ~~기존 세 구간을 네 알고리즘으로 동일 조건에서 다시 실행한다.~~ 완료
3. ~~기존 라벨과 재실행 결과가 일치하는지 검증한다.~~ 완료
4. 분석 ROI와 2×3 표시 격자의 관계를 확정한다.
5. ~~후보 생성 알고리즘 조합을 선정한다.~~ `robust_zscore + multiscale_top_hat + percentile_contrast`로 잠정 선정
6. ~~추가 라벨 표본을 만든다.~~ 두 번째 50건 큐 생성 완료
7. ~~두 번째 큐를 수동 라벨링하고 재순위화 기준을 검증한다.~~ 완료
8. 기술실증용 알고리즘 v0의 병합·순위점수와 임계값을 고정한다.
9. ATT·ATG 입력부터 JSON·CSV·PNG까지 단일 배치 명령으로 통합한다.
10. 고정된 결과 스키마를 INT-001·003·004 API에 연결한다.

