# 배치 분석 파이프라인 및 결과 JSON 스키마 v0

기준일: 2026-10-29  
상태: 내부 구현 초안 — VTI 협의 전

## 구현된 처리 흐름

1. 모니터링 데이터 ID와 같은 공통 파일명의 ATT·ATG를 입력한다.
2. 지정 구간의 ATT 프레임을 읽고 분석 ROI를 적용한다.
3. `robust_zscore`, `multiscale_top_hat`, `percentile_contrast` 후보를 생성한다.
4. 같은 프레임에서 겹치는 다중 알고리즘 후보를 병합한다.
5. 프레임 간 후보를 트래킹하고 지속성 점수로 우선순위를 정한다.
6. 대표 프레임에 ATG 시각·GPS를 결합한다.
7. 원본 해상도 열화상에 2×3 격자와 후보 영역을 렌더링한 PNG를 생성한다.
8. `analysis_result.json`, `analysis_candidates.csv`, 후보 PNG를 출력한다.

## 결과 파일

- 정식 결과: `analysis_result.json`
- 선행 기술실증·검토용: `analysis_candidates.csv`
- 후보 이미지: `images/{monitoringDataId}_{candidateId}_f{frame}.png`
- JSON Schema: `schemas/analysis-result-v0.schema.json`

## v0 주요 필드

- `schema_version`
- `monitoring_data_id`
- `status`, `success`, `completed_at`
- `processing`: 입력 파일명, 처리 프레임, 분석 ROI, 알고리즘과 주요 파라미터
- `candidate_count`
- `candidates[]`
  - 후보 ID와 검토 순위
  - 대표 프레임, 촬영시각, GPS
  - 분석 ROI와 후보 좌표
  - 최저·평균·최고·배경 온도 및 배경 대비 온도차
  - 탐지 알고리즘, 이상점수, 지속성 근거
  - 결과 PNG 경로·형식·해상도·격자 정보
- `warnings`, `error`

좌표는 이미지 좌측 상단을 원점으로 하는 픽셀 좌표이며 `x`, `y`, `width`,
`height`로 표현한다. `frame_range.end_exclusive`는 Python 슬라이스와 같이 끝
프레임을 포함하지 않는다.

## VTI·PM과 확정해야 할 항목

다음은 기술적으로 구현했지만 외부 계약으로는 아직 확정되지 않았다.

1. JSON 필드명 표기법과 `schema_version` 정책
2. 결과 이미지 `path`를 MinIO Object Key로 줄지 Presigned URL로 줄지
3. ATG가 없는 프레임의 후보를 `null` GPS로 제공할지 결과에서 제외할지
4. ATT·ATG 개수 불일치를 경고로 허용할지 작업 실패로 처리할지
5. 분석 대상 구간을 프레임 번호로 받을지 촬영시각으로 받을지
6. 분석 ROI 좌표 형식을 `x/y/width/height`로 사용할지 `x0/y0/x1/y1`로 사용할지
7. 후보 최대 제공 건수와 최소 트래킹 프레임 수
8. `status=FAILED`일 때 오류 객체와 처리정보의 필수 범위
9. CSV를 공식 연동 산출물로 유지할지 선행 기술실증에서만 사용할지

## 현재 기본 정책

- ATT·ATG는 프레임 인덱스로 결합한다.
- ATG가 짧으면 GPS와 촬영시각은 `null`로 제공하고 경고를 남긴다.
- 후보 온도차는 `후보 최고온도 - 후보 주변 5픽셀 영역의 중앙값`이다.
- 기본 분석 ROI는 영상 하단 45%이다.
- 결과 PNG는 열화상 원본 크기를 유지한다.
- 2×3 격자는 표시용이며 이상탐지 계산 ROI와 별개이다.

이 기본 정책은 VTI 협의 결과에 따라 스키마 버전을 올리지 않고도 구현 전
수정할 수 있다. 외부 연동 테스트가 시작된 후 호환성이 깨지는 변경은 별도
스키마 버전으로 관리한다.
