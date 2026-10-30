# DaonH&S 연동 INT-001~005 개발 범위

> **과거 초안 — 구현 기준으로 사용하지 않음**
>
> 2026-10-30 VTI 주간회의 결과 INT-002·005와 GIS/SHP·관로 매칭은
> DaonH&S 제공 범위에서 제외되었다. 현재 기준 문서는
> [`heat-pipe-service/docs/INT_001_003_004_DEVELOPMENT_SCOPE.md`](../../heat-pipe-service/docs/INT_001_003_004_DEVELOPMENT_SCOPE.md)다.

- 정리일: 2026-10-29
- 목적: VTI 시스템과 DaonH&S 이상징후 분석·GIS 시스템의 연동 개발 범위 정의
- 상태: 대체된 과거 초안

## 1. 전체 처리 흐름

1. VTI가 ATT·ATG 파일을 MinIO에 저장한다.
2. VTI가 INT-003으로 MinIO 참조정보와 회차 메타데이터를 전달하고 분석을 요청한다.
3. DaonH&S가 ACK와 작업 ID를 반환한다.
4. VTI가 INT-004를 폴링하여 작업 상태를 확인한다.
5. 분석 완료 후 VTI가 INT-001로 이상징후 후보 결과를 조회한다.
6. VTI가 INT-002로 후보별 관로 GIS 매칭 결과를 조회한다.
7. VTI가 INT-005로 관로 GeoJSON을 조회하여 지도에 표시하고 캐시한다.

초기 연동은 폴링 방식으로 구현하고, 필요하면 추후 콜백을 추가한다.

## 2. INT-001 — 이상징후 분석 결과 API

### 목적

완료된 이상징후 분석 결과와 후보 목록을 VTI에 제공한다.

### 엔드포인트 제안

```http
GET /api/v1/analysis-jobs/{jobId}/results
```

### 주요 응답

- 작업 ID
- 모니터링 데이터 ID
- 분석 성공 여부
- 분석 대상 및 처리 프레임 정보
- 후보 건수
- 후보별 정보
  - 후보 ID
  - 프레임 번호
  - 촬영시각
  - GPS
  - 이상점수 및 탐지 근거
  - ROI 좌표
  - 최고·평균·최저 온도 및 온도차
  - 결과 열화상 이미지 경로

### 결과 이미지

- DaonH&S 이상징후 탐지 모듈에서 직접 렌더링한다.
- 포맷: PNG
- 해상도: 열화상 원본 기준 384×288
- 표시 내용: ROI 격자와 이상징후 후보 영역
- 저장 위치: MinIO 결과 Bucket
- API 응답에는 Object Key 또는 접근 URL을 포함한다.

### 개발 항목

- 기존 후보 탐지 결과를 API 응답 스키마로 변환
- 후보별 대표 프레임 선정
- ATG 시각·GPS 결합
- ROI 및 후보 영역 렌더링
- PNG 생성 및 MinIO 저장
- 분석 결과 JSON 생성

## 3. INT-002 — GIS/SHP 매칭 결과 API

### 목적

이상징후 후보의 GPS 위치와 관로 공간정보를 매칭한 결과를 VTI에 제공한다.

### 엔드포인트 제안

```http
GET /api/v1/analysis-jobs/{jobId}/gis-matches
```

### 내부 처리

1. 후보 GPS 좌표를 확인한다.
2. WGS84 GPS를 관로 좌표계 EPSG:5186으로 변환한다.
3. 후보 위치와 관로 선형 간 최단거리를 계산한다.
4. 허용거리 내 관로를 검색한다.
5. 단일·복수·범위초과·미매칭 상태를 결정한다.

### 주요 응답

- 후보 ID
- 매칭 상태
- 관로 ID 및 관로 관리번호
- 관로까지의 거리
- 최근접 관로 지점 좌표
- 복수 후보 관로 목록
- 관로 상태 및 위험정보

### 매칭 상태 제안

```text
MATCHED
MULTIPLE_CANDIDATES
OUT_OF_RANGE
UNMATCHED
```

### 개발 항목

- SHP 로딩
- 좌표계 변환
- 공간 인덱스 생성
- 점–선 최단거리 계산
- 복수 관로 처리
- 매칭 결과 API 구성

### 확정할 업무 기준

- 관로 매칭 허용거리
- 복수 관로 반환 개수와 정렬 방식
- 대표 관로 ID로 `pipe_id`와 `pipe_no` 중 무엇을 사용할지
- GPS 오차를 거리 판정에 반영하는 방식
- 관로 상태·위험 코드 정의

## 4. INT-003 — ATT·ATG 연계 및 분석 실행 요청 API

### 목적

VTI가 MinIO에 저장한 ATT·ATG 파일의 참조정보와 회차 메타데이터를 전달하고 분석 작업을 생성한다.

### 엔드포인트 제안

```http
POST /api/v1/analysis-jobs
```

### 주요 요청

- 모니터링 데이터 ID: ATT·ATG의 확장자를 제외한 공통 파일명
- ATT Object Key 또는 Presigned URL
- ATG Object Key 또는 Presigned URL
- 파일 크기 및 체크섬
- 분석 대상 구간
- ROI 설정
- 선택적 콜백 URL

```json
{
  "monitoringDataId": "20260813_140209",
  "files": {
    "att": {
      "objectKey": "monitoring/20260813_140209.att",
      "presignedUrl": "https://minio.example/..."
    },
    "atg": {
      "objectKey": "monitoring/20260813_140209.atg",
      "presignedUrl": "https://minio.example/..."
    }
  },
  "segmentId": "SEG-001",
  "roi": {
    "rows": 2,
    "columns": 3
  }
}
```

### 주요 응답

```json
{
  "jobId": "JOB-0001",
  "monitoringDataId": "20260813_140209",
  "status": "QUEUED"
}
```

### 개발 항목

- 요청값 검증
- MinIO 접근 및 파일 다운로드
- 파일명·크기·체크섬 확인
- ATT·ATG 구조와 레코드 수 검사
- 중복 요청 방지
- 분석 작업 생성 및 작업 ID 발급
- 백그라운드 분석 실행

## 5. INT-004 — 상태 동기화 및 오류 처리 API

### 목적

VTI가 분석 진행 상태와 오류를 확인하고 재시도 여부를 판단할 수 있도록 한다.

### 엔드포인트 제안

```http
GET /api/v1/analysis-jobs/{jobId}
```

### 상태값 제안

```text
QUEUED
RUNNING
COMPLETED
FAILED
CANCELLED
```

### 주요 응답

- 작업 ID
- 모니터링 데이터 ID
- 현재 상태와 진행률
- 전체·처리 프레임 수
- 작업 시작·완료시각
- 오류 코드와 메시지
- 재시도 가능 여부

### 공통 오류 응답 예시

```json
{
  "timestamp": "2026-11-09T10:00:00+09:00",
  "status": 422,
  "code": "ATT_ATG_MISMATCH",
  "message": "ATT 프레임 수와 ATG 레코드 수가 일치하지 않습니다.",
  "jobId": "JOB-0001",
  "retryable": false
}
```

### 주요 오류 코드 제안

- `INVALID_REQUEST`
- `UNAUTHORIZED`
- `FILE_NOT_FOUND`
- `MINIO_ACCESS_FAILED`
- `INVALID_FILE_FORMAT`
- `ATT_ATG_MISMATCH`
- `ANALYSIS_FAILED`
- `GIS_DATA_UNAVAILABLE`
- `CRS_TRANSFORM_FAILED`
- `SERVICE_UNAVAILABLE`

### 개발 항목

- 작업 상태 영속 저장
- 진행률 계산
- 예외별 오류 코드 변환
- 타임아웃 처리
- 재시도 가능 여부 관리
- 중단 후 재실행 처리
- 로그 및 장애 추적

## 6. INT-005 — 관로 GeoJSON API

### 목적

관로 공간정보를 웹 지도에 표시할 수 있도록 GeoJSON으로 제공한다.

### 엔드포인트 제안

```http
GET /api/v1/pipelines/geojson
GET /api/v1/pipelines/geojson?bbox=...&segmentId=...
```

### 내부 처리

- 통합 관로 SHP 로딩
- 원본 좌표계 EPSG:5186 적용
- 웹 지도용 EPSG:4326 변환
- GeoJSON 생성
- 결과 캐시
- 갱신일 또는 버전 관리

### 주요 GeoJSON 속성 제안

- `pipe_id`
- `pipe_no`
- `pipe_secti`
- `pipegrp_id`
- `dia_cd`
- `instl_ymd`
- `pipe_len`
- `min_dep`, `max_dep`, `avg_dep`
- `eqp_st_cd`
- `risk_score`, `risk_grd`

### 개발 항목

- SHP·DBF·PRJ 파싱
- 좌표계 변환
- GeoJSON 생성
- 노출 속성 선별
- BBox 또는 구간별 조회
- 서버 캐시
- 데이터 갱신 처리

## 7. 공통 개발 범위

### API 기반

- FastAPI 등 API 서버
- OpenAPI/Swagger 문서
- 요청·응답 모델
- 공통 오류 응답
- API 버전 관리

### 인증

- 기술실증 단계: `Authorization: Bearer {API_KEY}`
- HTTPS
- API Key 발급·폐기
- 필요시 VTI 서버 IP 제한
- MinIO Presigned URL 유효시간 관리
- 로그에 인증정보를 기록하지 않음

### 작업 실행

- 분석 작업 Queue
- 동시 실행 작업 수 제한
- 백그라운드 실행
- 작업 상태 영속 저장
- 서버 재시작 후 작업상태 복구

### 저장소

- VTI MinIO 입력파일 접근
- 결과 PNG 저장
- Object Key 규칙
- 결과 보관기간 및 삭제 정책

### 배포

- Docker 이미지
- 환경변수 설정
- 개발·운영 환경 분리
- Health Check 및 버전 API
- 실행 로그
- 설치·운영 문서

권장 보조 엔드포인트:

```http
GET /health
GET /version
GET /openapi.json
```

## 8. 현재 확보된 기능·데이터

- ATT 파서
- ATG 시간·GPS 파서
- ROI 기반 열이상 후보 추출
- 후보 프레임 추적·순위화
- CSV·JSON 결과 생성 기반
- 통합 관로 SHP 637건
- 관로 좌표계 EPSG:5186
- 관로 ID 및 주요 속성정보

관로 원본은 `PIPE_1구간`~`PIPE_4구간`으로 구성되어 있으며, 네 `.prj`가 모두 `Korea 2000 / Central Belt 2010`으로 동일하다. 네 원본의 레코드 합계와 좌표 범위가 통합본과 정확히 일치하므로 통합본에도 같은 EPSG:5186을 적용한다.

## 9. 새로 개발해야 하는 기능

- API 서버 전체
- MinIO 연동
- 작업 Queue와 상태 관리
- Bearer 인증
- 공통 오류 체계
- 결과 PNG 렌더링 및 업로드
- SHP→GeoJSON 변환
- GPS–관로 최근접 매칭
- OpenAPI 명세
- Docker 배포
- 통합 연동 테스트

## 10. 일정 관련 기존 협의

- INT-001~005 API 명세서 제공 일정과 연동 테스트용 실제 엔드포인트 제공 일정을 구분해 안내한다.
- 선행 기술실증은 2026년 11월 9일을 목표로 검토한다.
- 모듈 자체 개발과 VTI 시스템 탑재·통합 테스트는 별개의 일정으로 관리한다.

