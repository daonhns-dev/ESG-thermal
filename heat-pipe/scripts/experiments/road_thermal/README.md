# road_thermal — 재방문 정합·검출 성립성 실험 (설계 합의 v1)

이 폴더는 `re_testbed`의 운영 검토 흐름이 아니다. `re_testbed/README.md`의
"Deferred Work"(반복 통과 비교는 신뢰할 만한 RGB/열화상 랜드마크 정합이 생기기
전까지 보류)에서 말하는 **재개 조건이 실제로 성립하는지를 검증하는 연구 실험**이다.

- 운영 코드(`re_testbed/`)는 건드리지 않는다.
- 기존 파서(`datasets/att_atg_io.py`)와 점수맵(`re_testbed/algorithms.py`)을
  재사용한다. 후보 추출/트래킹 로직을 여기서 다시 구현하지 않는다.
- 현재 상태: ① 원본 품질검사, ② 수동 이벤트 기반 동기화 평가와
  자동 오프셋 후보 시범을 구현. ④ 정합, ⑤ 합성 주입, ⑥ 변화 비교는 미구현.

## 1차 구현 사용법

`heat-pipe/`에서 실행한다. Python 환경에는 기존 프로젝트 의존성인 `numpy`,
`opencv-python`이 필요하다. 스크립트는 원본 파일을 수정하지 않으며, 출력 파일이
이미 있으면 덮어쓰지 않고 중단한다.
이 PC에서는 기본 `python`이 두 패키지를 제공하지 않아
`C:\Users\USER\anaconda3\python.exe`로 검증했다. 아래 명령의 `python`은 해당
인터프리터나 같은 패키지를 갖춘 프로젝트 인터프리터로 바꿔 실행한다.

```powershell
python scripts/experiments/road_thermal/session_qc.py `
  --source "E:\heatpipe\20260813" `
  --output results/road_thermal/qc_20260813.json
```

품질 보고서에는 ATT/ATG/AVI의 프레임·레코드 수, ATG 시각의 역행·1초 초과
공백, 평균 **기록** 프레임율, GPS 좌표 형식 오류, 열화상 0프레임 표본이 담긴다.
1초 초과 시각 공백은 주의 신호이며, 그 자체만으로 파일 손상을 확정하지 않는다.
기본 `--zero-sample-stride 30`은 표본 검사다. 0프레임을 전수 확인하려면
`--zero-sample-stride 1`로 별도 출력 파일을 만든다. AVI 프레임 수는 컨테이너
메타데이터값이며 전 프레임 디코딩 성공을 증명하지 않는다.

동기화 시험에는 두 영상 모두에 보이는 **같은 물리적 사건**을 독립적으로
표시한 CSV가 필요하다. 프레임 번호는 0부터 시작한다. `rgb_uncertainty_frames`와
`thermal_uncertainty_frames`는 각 영상에서 사건 시점을 표시할 때의 오차 범위다.
다음은 CSV 헤더이며, 실제 사건 확인 전에는 예시 프레임 번호를 만들어 넣지 않는다.
동일한 헤더가 `events_template.csv`에도 들어 있다. 헤더만 있는 파일을 평가하면
모든 세션은 미확인으로 보고된다.

```csv
session,event_id,rgb_idx,thermal_idx,rgb_uncertainty_frames,thermal_uncertainty_frames,description
```

세션의 초·중·후반에 걸친 사건을 기록한 뒤 평가한다.

```powershell
python scripts/experiments/road_thermal/sync_audit.py `
  --manifest results/road_thermal/qc_20260813.json `
  --events results/road_thermal/shared_events.csv `
  --output results/road_thermal/sync_20260813.json
```

`constant_offset_compatible`은 모든 표기 오차 범위와 양립하는 고정 오프셋이
있다는 뜻, `linear_drift_compatible`은 선형 변화가 표기 오차 범위에 맞는다는 뜻이다.
`inconsistent_offsets`는 둘 다 설명하지 못한다. 세 구간 중 하나라도 빠지거나
사건이 3개 미만이면 `unconfirmed_insufficient_coverage`다. 어느 분류도 실제
노출 시각의 동시성을 **증명**하지 않으며, 사건이 없으면 미확인으로 남긴다.

## 모션 기반 오프셋 후보 시범

`motion_sync.py`는 원본 AVI와 ATT를 각 한 번씩 순차로 읽어 프레임 변화량을
비교한다. 기본 탐색 범위는 `thermal_idx - rgb_idx = -60..60`프레임이다.
결과의 `full/early/middle/late` 피크는 검토할 **후보**이지 동기화 판정이 아니다.
화각·장면·노출 차이 때문에 높은 상관값만으로 같은 순간을 증명할 수 없다.
시범은 두 세션에서 각각 실행하며 `--output`은 서로 다른 새 경로를 쓴다.
새 보고서의 `nearby_offset_scores`는 최고 후보 주변 ±2프레임의 원점수,
`all_offset_scores`는 전체 탐색 범위의 원점수다. 기존
`best_minus_second_correlation`은 ±2프레임을 제외한 *서로 떨어진*
후보의 격차이지 인접 오프셋 간 격차가 아니다.
`rgb_activity_suggestions`는 RGB 변화량만으로 뽑은 검토 시작점이며,
양쪽 영상에 공통 사건이 있다는 뜻은 아니다.

```powershell
& 'C:\Users\USER\anaconda3\python.exe' scripts/experiments/road_thermal/motion_sync.py analyze `
  --avi 'E:\heatpipe\20260813\20260813_140209.avi' `
  --att 'E:\heatpipe\20260813\20260813_140209.att' `
  --output results/road_thermal/motion_140209.json
```

출력에는 전수 확인한 0프레임의 ATT 인덱스 목록과 렌더링 MP4의 예상
프레임 수도 담긴다. 기존 `render_thermal_video.py`가 만든 MP4의 0기준
프레임 번호를 원본 ATT 번호로 바꾸려면 다음 명령을 쓴다.

```powershell
& 'C:\Users\USER\anaconda3\python.exe' scripts/experiments/road_thermal/motion_sync.py map `
  --report results/road_thermal/motion_140209.json --rendered-index 1000
```

이 변환은 렌더러가 0프레임만 건너뛰고 실제 MP4가 모두 기록·디코딩됐다는
전제에서 성립한다. 육안 확인 전에 실제 MP4의 디코딩 프레임 수가 보고서의
`expected_rendered_mp4_frames`와 같은지 확인한다. RGB AVI는 원본 자체를
사용하므로 이 0프레임 보정이 필요 없다. 시범 도구는 AVI의 디코딩 프레임 수와
ATT 프레임 수도 대조한다. `events_template.csv`에는 확정된 원본 AVI/ATT
인덱스만 기록하며, 자동 피크에서 임의로 사건 번호를 채우지 않는다.

프레임 번호를 직접 확인하려면 MP4 대신 원본 AVI/ATT에서 번호가 적힌 비교
이미지를 뽑는다. 기본값은 세션의 초·중·후반 중앙 프레임과 앞뒤 4프레임을
2프레임 간격으로 보여준다. RGB 아래 행에는 자동 최고 후보와 그 양옆
오프셋의 열화상 프레임을 나란히 배치한다. 프레임 번호는 모두 0부터 시작한다.

```powershell
& 'C:\Users\USER\anaconda3\python.exe' scripts/experiments/road_thermal/frame_review.py `
  --report results/road_thermal/motion_140209.json `
  --output-dir results/road_thermal/review_140209
```

특정 움직임을 더 자세히 보려면 `--indices 4300 12900 21500 --radius 10
--step 1`처럼 다시 뽑되 **새 출력 폴더**를 쓴다. `--offsets -1 0`으로
열화상 행을 직접 지정할 수도 있다. 비교 이미지는 시간 동기화의 확정
증거가 아니라, 두 영상에 공통으로 보이는 사건을 찾고 독립적으로 번호를
주석 처리하기 위한 자료다.

양쪽 사건 프레임을 **독립적으로** 읽을 때는 `event_review.py`로 각 모달리티의
이미지를 별도 파일로 뽑는다. 지정 범위의 모든 프레임을 6장씩 묶어 번호를
표시하고, 지정한 원본 픽셀 영역을 최근접 보간으로 확대한다. 확대는 새로운
장면 정보를 만들지 않는다. 다음 예시는 140209 후반 횡단보도 구간이다.

```powershell
& 'C:\Users\USER\anaconda3\python.exe' scripts/experiments/road_thermal/event_review.py `
  --report results/road_thermal/motion_140209_v2.json `
  --rgb-range 21518 21540 --thermal-range 21518 21540 `
  --rgb-crop 80 270 350 450 --thermal-crop 20 110 190 230 `
  --output-dir results/road_thermal/event_140209_crossing
```

RGB와 열화상에서 같은 **물리적 순간**을 별도로 표시할 수 있을 때만
`events_template.csv`에 원본 인덱스와 표기 오차를 기록한다. 화각 경계 진입이나
서로 다른 물체의 위치를 사건으로 간주하지 않는다. 잘 안 보이면 미확인으로 둔다.
열화상 도색 대비를 시각적으로 점검할 때는 `--thermal-scale-crop X1 Y1 X2 Y2`로
색상 범위 산출 영역을 도로 위주로 제한할 수 있다. 1~99퍼센타일은 선택한
**모든 프레임을 합쳐 한 번만** 계산하므로 프레임별 색상 깜빡임은 없다.
출력 폴더의 `thermal_scale.json`에 적용한 영역과 온도 범위를 기록한다.
이 시각화가 도색의 온도차나 동기화를 증명하지는 않는다.

## 목표 / 비목표

- **목표**: 도로 표면 열이상 검토 후보를 찾고, 같은 지점의 과거 촬영과 비교
  가능한지 검증해 사람이 검토할 순위 목록을 만든다.
- **비목표**: 열화상만으로 매설관 결함·누수를 자동 확진하지 않는다.
- **출력 명칭 규율**:
  - 항상: **도로 표면 열이상 후보**
  - 배관 GIS 근접이 확인된 경우: **배관 경로 인근 도로 열이상 후보**
    (여전히 "결함"이라 부르지 않는다)
  - "실제 배관 결함"이라는 표현은 보수 기록 또는 현장 확인처럼 원인을
    뒷받침하는 근거가 추가로 있을 때만 쓴다.

## 확인된 사실

| 항목 | 확인값 |
|---|---|
| 8/13 세션 | 13개(avi 보유 9개), `.att` 전체 프레임 81,809, avi 보유 9세션 54,146 |
| 야간 세션 | 6개, 23,063프레임(22:57~00:00) |
| 8/12~8/18 데이터 | 로컬에 이미 존재 (8/14 포함) — 재방문 비교에 추가 데이터 확보 불필요 |
| GPS(`.atg`) 정확도 | 5~30m대 — [`docs/EXPERIMENT_SUMMARY.md`](../../../docs/EXPERIMENT_SUMMARY.md)에 기록된 **현장 오차 추정 범위**이지, 이 장비를 기준점과 대조해 확정한 스펙이 아님 |
| avi 컨테이너 fps | 신뢰 불가. `230106`: avi 재생시간 5분32초 vs `.atg` 실측 13분2초 — **경과시간은 반드시 `.atg` 기준** |
| avi/att/atg "인덱스 1:1 대응" | 기존 코드(`datasets/att_atg_io.py`, `scripts/build_rgb_thermal_dataset.py`)가 검증한 건 **총 프레임/레코드 개수 일치뿐**. 인덱스 i가 세 파일 모두에서 같은 촬영 순간이라는 건 미검증. `.atg`도 avi/att와 같은 방식(개수만 일치 확인)으로 1:1이라 주장되므로, GPS를 RGB 프레임에 붙여 장소를 검색할 때도 이 문제가 그대로 적용됨 |
| `raw/100` (`.att` 온도) | 저장 양자화 단위. 센서 실제 정확도(NETD)와 다름 |
| `re_testbed` 기존 라벨 | 50건(`vehicle` 16 / `road_paint` 23 / `road_surface_hotspot` 11). **의도적 다양성 샘플링**이라 모집단 탐지율로 쓸 수 없음 |
| `likely_ground_fixed` | [`utils/thermal_tracks.py:87-93`](../../../utils/thermal_tracks.py) — `len(points)>=3 and growth>=growth_thresh and exits_bottom and y_end>y_start and mean_circularity>=min_circularity`, **5조건 AND 하드게이트**. "0건"은 "고정 열원 없음"이 아니라 "이 5조건 조합 미충족"으로만 해석 |

## 실험 순서와 의존성

```
① 원본 품질·시간축 검사 (.atg 기준, 0패딩/누락 확인)
        │
        ├─▶ ② 동기화 시험 (RGB↔열화상 인덱스가 같은 순간인지)
        │         │
        │         └─▶ ④-a 같은 프레임 RGB↔열화상 정합
        │              (② 확정 전까지 잠정치로만 표시)
        │
        ├─▶ ④-b 같은 세션 시간차 RGB↔RGB, 열화상↔열화상 정합  (②와 독립, 바로 진행 가능)
        ├─▶ ④-c 다른 날짜 같은 구간 RGB↔RGB, 열화상↔열화상 정합 (②와 독립, 바로 진행 가능)
        │
        └─▶ ⑤ 열화상 합성 주입 + 기존 4개 알고리즘 비교 (완전 독립, 병렬 진행)

⑥ 재방문 변화비교는 해당 정합 경로(④-b/c)가 검증된 구간에서만 수행
```

**핵심 의존성 규칙**: 동기화(②)에 실제로 의존하는 건 ④-a(같은 프레임 RGB↔열화상)
뿐이다. ④-b, ④-c(단일 카메라 내부 비교)와 ⑤(순수 열화상 합성)는 ②와 무관하게
바로 진행한다.

## ② 동기화 시험

**방법**: 세션 초·중·후반에서 두 영상 모두에서 *실제로 확인되는* 동일한
물리적 사건을 각 영상에서 독립적으로 프레임 번호로 표시하고,
`열화상 프레임번호 - RGB 프레임번호`가 고정값인지(오프셋), 서서히 변하는지
(드리프트), 불규칙한지 확인한다. 픽셀 위치가 아니라 **이벤트 발생 순서**만
비교 대상으로 삼아 두 카메라의 화각·시차 차이를 우회한다.

**기각된 이벤트 후보와 사유** (다시 시도하지 말 것):

| 후보 | 기각 사유 |
|---|---|
| 화면 가장자리 통과 | RGB와 열화상 화각이 달라 같은 실세계 순간에도 가장자리를 넘는 프레임이 서로 다름 |
| 신호등 색 전환 | 열화상에서 관측 보장 안 됨(가시광 신호를 열복사로 못 볼 수 있음) |
| 그림자 진입/이탈 | RGB 밝기는 즉시 바뀌지만 노면 온도는 열관성으로 서서히 바뀜 — 열화상에서 계단형 변화로 안 나타남 |
| 가로등 통과 | 열화상에 안정적으로 안 잡히고 국소 신호라 전역 이벤트 아님 |
| 터널 진입(전역 조도 변화) | 실세계로는 순간적이어도 카메라별 자동노출/자동보정(AGC/NUC) 반응 속도가 달라 검출 프레임이 어긋날 수 있음 |

**적합한 이벤트가 없으면** 억지로 채택하지 말고 해당 구간의 동기화 상태를
**"미확인"**으로 남긴다.

## ④ 정합 벤치마크

3분류를 **분리 평가**한다: (a) 같은 프레임 RGB↔열화상, (b) 같은 세션 시간차
RGB↔RGB·열화상↔열화상, (c) 다른 날짜 같은 구간 RGB↔RGB·열화상↔열화상.

- GPS는 (c)의 후보 구간 탐색에만 쓰고(반경 여러 값 비교), 최종 확인은 영상
  속 고정 지물로 한다.
- **선정에 쓴 지물과 오차 검증용 대응점을 분리**한다.
- GPS 후보 중 실제로 다른 장소로 확인된 **음성 쌍**을 포함해 파이프라인의
  오매칭 특이도를 측정한다.
- 표본 단위는 인접 프레임이 아닌 **서로 떨어진 도로 구간**(최소 분리 기준을
  결과 확인 전에 사전등록).
- 실패 쌍을 성공률 분모에서 빼지 않는다.

**보고 지표 (5개, 전부 필요)**:
1. GPS 검색 반경별 같은 장소 후보 회수율 vs 검토량
2. 다른 장소 오매칭률
3. 정합 성공률(실패 쌍 포함 분모)
4. 기하 정합 오차 비율 = (정합오차 px) / (탐지 대상 패턴 폭 px) — 여러 폭에서 제시, **이 값 하나로 합격 선언하지 않음**
5. 유효 도로 영역

## ⑤ 검출기 시험

`re_testbed/algorithms.py`의 4개 점수맵(`robust_zscore`, `top_hat_zscore`,
`multiscale_top_hat`, `percentile_contrast`)을 그대로 재사용해, 여러
폭·방향·온도차의 합성 신호를 주입하고 비교한다.

- 기존 50건 라벨은 오탐 유형 분석 전용, 탐지율 근거로 사용 금지.
- 구간 분리 기준의 **독립 평가 표본**을 별도로 구성한다.
- 합성 신호 포착 성공은 **영상처리 능력의 증거일 뿐, 실제 누수의 지표 신호
  존재를 증명하지 않는다.**
- 결과는 "검토 부담(허용 가능한 후보 수)별 포착률"로 보고한다.

## ⑥ 재방문 변화비교

④-b/c 정합이 검증된 구간에서만 수행. 원본 온도차와 정규화 온도차를 함께
보존하고, ④의 "기하 정합 오차 비율"을 검토 목록에 같이 노출한다.

## 아직 정할 것

- 공통 이벤트 선정 기준 (위 기각 목록 외의 실제 후보는 영상을 봐야 확정)
- 독립 도로 구간의 분리·표본 기준(최소 분리 거리/시간)
- 검토자가 감당 가능한 후보 수
- 시험할 열패턴 폭 범위와 정합 통과 기준(고정 합격선 없음, 상대 비율로 판단)

이 값들은 결과를 보기 전에 실험 계획에 기록한다.
