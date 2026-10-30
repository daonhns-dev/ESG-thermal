# 이상탐지 코어 이관 기록

기준일: 2026-10-29

## 목적

PoC인 `heat-pipe`에서 확인된 이상탐지 코어만 운영 서비스 후보인
`heat-pipe-service`로 이관한다. 운영 코드는 PoC 패키지를 런타임에
참조하지 않는다.

## 이관 범위

| 서비스 모듈 | PoC 기준 코드 | 역할 |
|---|---|---|
| `analysis/io.py` | `datasets/att_atg_io.py` | ATT 헤더·프레임 및 ATG 레코드 읽기 |
| `analysis/anomaly.py` | `utils/thermal_anomaly.py` | 배경 추정과 robust z-score 계산 |
| `analysis/algorithms.py` | `re_testbed/algorithms.py` | 이상 점수맵 생성 |
| `analysis/candidates.py` | `re_testbed/candidate.py` | 연결 성분 기반 후보 추출 |
| `analysis/tracking.py` | `re_testbed/tracking.py` | 프레임 간 후보 트래킹과 요약 |
| `analysis/ranking.py` | `re_testbed/score_and_rank.py` | 검토 우선순위 점수 산출 |
| `analysis/roi.py` | `re_testbed/roi.py` | 분석·검토 ROI 계산 |
| `analysis/rendering.py` | PoC 시각화 유틸리티 | PNG 렌더링용 열화상 정규화·ROI 표시 |

초기 후보 생성 알고리즘은 라벨 100건 검토 결과를 반영하여
`robust_zscore`, `multiscale_top_hat`, `percentile_contrast` 세 가지로
고정한다. `top_hat_zscore`는 기존 결과 재현을 위해 지원하지만 초기 후보
생성 목록에서는 제외한다.

## 검증 결과

- 서비스 단위·Golden Test: `6 passed`
- 합성 데이터에서 PoC와 점수맵, 후보, 순위, ROI 결과가 수치상 완전 일치
- 실제 데이터 `E:\heatpipe\20260813\20260813_132634.att` 첫 프레임에서
  4개 점수맵과 후보 43건이 PoC 결과와 완전 일치
- Python 바이트코드 컴파일 성공

## 확인된 데이터 주의사항

위 실제 파일은 ATT 프레임 3,959개와 ATG 레코드 3,885개로 개수가 다르다.
따라서 이후 배치 처리 계층에서는 프레임 번호만으로 두 파일을 무조건
1:1 결합하지 않고, 레코드 수 검증과 불일치 정책을 명시해야 한다.

## 코어 이관 이후의 범위

- 후속 구현: INT-001·003·004 API, 파일 저장소 연동, 비동기 작업 큐와 재시도
- 최종 제외: INT-002·005, GIS/SHP·GeoJSON, GPS–관로 매칭

후속 구현 기능은 이 코어 위에 별도 애플리케이션 계층으로 구성한다. 최종
범위는 [`INT_001_003_004_DEVELOPMENT_SCOPE.md`](INT_001_003_004_DEVELOPMENT_SCOPE.md)를
따른다.
