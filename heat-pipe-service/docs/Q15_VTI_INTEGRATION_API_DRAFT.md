# Q15 VTI–DaonH&S 연동 API 명세 초안

- 작성일: 2026-11-02
- 버전: v0.2 수정 초안. 이전 전달 초안의 기본 ROI 예시를 하단 가운데 칸으로 바로잡음.
- 상태: **VTI 사전 검토용 초안**. 2026-11-06 확정본 전에 전달하여 연동 방식과 미확정 항목에 대한 의견을 받기 위한 문서다. 현재 코드 동작과 `확정 필요` 제안을 구분한다.
- 기준: [INT-001·003·004 개발 범위](INT_001_003_004_DEVELOPMENT_SCOPE.md), [OpenAPI 사본](openapi.json), [결과 JSON Schema v0](schemas/analysis-result-v0.schema.json)
- 범위: INT-003 분석 요청, INT-004 상태 조회, INT-001 결과·PNG 조회. GIS/SHP, INT-002·005는 제외한다.

## 1. 연동 방향과 역할

```text
VTI: ATT·ATG를 MinIO에 업로드
  → VTI 백엔드: 파일 참조와 monitoring_data_id를 INT-003에 전송
  ← DaonH&S: HTTP 202 접수 확인(ACK)과 job_id 반환
  → VTI 백엔드: INT-004 상태·진행률 조회
  → SUCCEEDED 후 INT-001 결과 JSON 및 후보 PNG 조회
  → VTI: 후보 ID를 유지하고 관로 매칭·최종 판정·화면 표시
```

**파일 전송 기본안(확정 필요):** VTI가 ATT·ATG를 자체 MinIO에 업로드한 뒤 각각의 **Presigned HTTPS GET URL**을 INT-003에 보낸다. 약 770MB인 ATT 본문은 API에 직접 업로드하지 않는다. DaonH&S 분석 워커가 URL에서 파일을 내려받는다. 이 방식에는 DaonH&S의 VTI MinIO 계정이 필요하지 않다. `object_key`도 구현되어 있으나 공유 로컬 저장소 또는 MinIO 읽기 권한이 따로 필요하므로 연동 기본안은 URL이다. URL 호스트는 DaonH&S의 허용 목록에 등록해야 하며 HTTP와 리다이렉트는 허용되지 않는다. URL 유효기간은 작업 대기·다운로드·자동 재시도를 포함하도록 양측이 정한다.

**결과 전달 기본안(확정 필요):** DaonH&S가 결과 JSON·CSV·PNG를 자체 저장소에 보관하고 VTI 백엔드가 Bearer 인증으로 JSON과 PNG를 API에서 읽는다. VTI가 결과 저장소나 MinIO에 직접 접근할 필요는 없다. `result_image.path`는 API 기준 주소에 붙일 상대 경로다. VTI 프런트엔드에는 Bearer 키를 전달하지 않고 VTI 백엔드에서 PNG를 중계한다. CSV는 내부 저장 산출물이며 이 초안의 API 조회 대상은 아니다.

## 2. 공통 규칙

| 항목 | 현재 구현 및 제안 |
|---|---|
| 기준 주소 | 배포 시 합의할 DaonH&S HTTPS 주소. 아래 예시는 `https://api.daon.example` 자리표시자다. |
| 데이터 | JSON, UTF-8. 성공 시각은 ISO 8601 형식이며 현재 서버 생성 시각은 UTC다. |
| 인증 | INT-001·003·004와 PNG 조회에 `Authorization: Bearer <API_KEY>` 필수. VTI 백엔드와 DaonH&S 서버만 키를 보관한다. |
| 공개 경로 | `GET /health`, `GET /version`, `GET /docs`, `GET /openapi.json` |
| 오류 본문 | `{"error":{"code":"...","message":"..."}}` |
| 작업 ID | 현재 `JOB-YYYYMMDD-NNNNNN`. 새 INT-003 요청마다 새 ID가 발급된다. |
| 후보 ID | 현재 DaonH&S가 `JOB-YYYYMMDD-NNNNNN-CAND-NNNN` 생성. 재분석은 새 작업이므로 동일 후보 ID 유지가 보장되지 않는다. |

`/docs`와 `/openapi.json`은 서버가 실행 중일 때 기준 주소에 붙여 조회한다. 정적 사본은 [openapi.json](openapi.json)이다. 별도 분석 워커가 가동되어야 접수 작업이 진행된다.

## 3. INT-003: 분석 요청 및 접수 확인

`POST /api/v1/analysis-jobs` → **HTTP 202 Accepted**. 202는 작업 접수와 ID 발급을 뜻하며 분석 완료를 뜻하지 않는다.

| 요청 필드 | 필수 | 형식·의미 |
|---|---:|---|
| `monitoring_data_id` | 예 | VTI의 회차/모니터링 데이터 식별자. 영문·숫자·`_`·`-`만 허용. |
| `att`, `atg` | 예 | 파일별 참조 객체. 각 객체에서 `url` 또는 `object_key` **정확히 하나**를 지정. |
| `att.url`, `atg.url` | 기본안 | HTTPS GET URL. 서버는 허용 호스트만 다운로드하고 리다이렉트를 따르지 않는다. |
| `att.object_key`, `atg.object_key` | 대안 | 서버가 접근 가능한 저장소의 상대 Object Key. URL과 동시 지정 불가. |
| `size_bytes` | 아니요 | 양의 정수. 내려받은 파일 크기 검증. |
| `sha256` | 아니요 | 64자리 16진수. 내려받은 파일의 SHA-256 검증. |
| `frame_range.start` | 아니요 | 0부터 시작하는 프레임 인덱스. 기본 0. |
| `frame_range.end_exclusive` | 아니요 | 끝 프레임을 **포함하지 않는** 인덱스. 생략 시 끝까지 분석. `start`보다 커야 한다. |
| `analysis_roi` | 아니요 | 픽셀 좌표 `{x,y,width,height}`. 좌상단 원점, `x/y ≥ 0`, `width/height ≥ 1`. 생략 시 전체 화면의 2행×3열 격자 중 하단 가운데 칸을 분석한다(384×288 입력은 `x=128,y=144,width=128,height=144`). 명시적 ROI도 그 칸 내부여야 하며, 밖이면 작업이 `FAILED`로 끝난다. |

URL은 요청 모델에서 URL 형식을 검사하고, 실제 HTTPS·허용 호스트 검사는 작업 실행 시 이뤄진다. 따라서 INT-003이 202를 반환해도 다운로드나 파일 무결성 검증에 실패하면 INT-004가 최종 `FAILED`를 반환할 수 있다.

예시 요청(주소·토큰·서명은 자리표시자):

```http
POST /api/v1/analysis-jobs HTTP/1.1
Authorization: Bearer <API_KEY>
Content-Type: application/json

{
  "monitoring_data_id": "20260813_132634",
  "att": {"url": "https://minio.vti.example/inputs/20260813_132634.att?signature=...", "size_bytes": 875667464},
  "atg": {"url": "https://minio.vti.example/inputs/20260813_132634.atg?signature=...", "size_bytes": 151552},
  "frame_range": {"start": 0, "end_exclusive": 30},
  "analysis_roi": {"x": 128, "y": 144, "width": 128, "height": 144}
}
```

예시 응답:

```json
{
  "job_id": "JOB-20261102-000001",
  "monitoring_data_id": "20260813_132634",
  "status": "QUEUED",
  "created_at": "2026-11-02T01:00:00Z"
}
```

재전송 중복 방지용 `Idempotency-Key`는 현재 구현되어 있지 않다. 응답이 유실된 경우 같은 요청을 다시 POST하면 별도 작업이 생긴다. VTI는 수신한 `job_id`를 보관하고 재요청 정책을 협의해야 한다.

## 4. INT-004: 작업 상태·진행률·실패 조회

`GET /api/v1/analysis-jobs/{job_id}` → **HTTP 200**.

| 응답 필드 | 의미 |
|---|---|
| `job_id`, `monitoring_data_id` | 작업과 VTI 회차 식별자 |
| `status` | `QUEUED`, `VALIDATING`, `DOWNLOADING`, `ANALYZING`, `RENDERING`, `UPLOADING`, `SUCCEEDED`, `FAILED` 중 하나 |
| `progress_percent` | 정수 0~100. 처리 단계 표시값이며 남은 시간 예측치는 아니다. |
| `processed_frame_count` | 유효하게 분석한 프레임 수. 빈 프레임은 제외. |
| `total_frame_count` | 선택된 프레임 구간의 전체 수. 분석 전에는 `null`일 수 있다. |
| `status_message` | 현재 단계 설명 |
| `created_at`, `started_at`, `completed_at` | 생성·시작·종료 시각. 미도달 단계는 `null`. |
| `error` | 최종 실패 시 `{code,message}`. 그 외에는 `null`. |

초기 상태는 `QUEUED`·0%다. 현재 단계값은 대략 검증 1%, 다운로드 5%, 분석 10~85%, 렌더링 90%, 결과 저장 95%, 성공 100%다. 자동 재시도 때 같은 `job_id`가 `QUEUED`·0%로 돌아갈 수 있다. 최대 3회 시도하며 다운로드·업로드·입출력 오류 및 실행 중단 등 일부 오류만 재시도한다. 최종 `FAILED`이면 `error`를 확인한다. 상세 정책은 [작업 상태 문서](JOB_STATE_V0.md)를 참조한다.

예시 응답:

```json
{
  "job_id": "JOB-20261102-000001",
  "monitoring_data_id": "20260813_132634",
  "status": "ANALYZING",
  "created_at": "2026-11-02T01:00:00Z",
  "progress_percent": 52,
  "processed_frame_count": 15,
  "total_frame_count": 30,
  "status_message": "Analyzing ATT frames",
  "started_at": "2026-11-02T01:00:05Z",
  "completed_at": null,
  "error": null
}
```

**분석 완료 알림 기본안(확정 필요):** 콜백 없이 VTI가 이 경로를 주기적으로 조회한다. `SUCCEEDED`일 때 INT-001을 호출하고 `FAILED`일 때 오류를 표시한다. 폴링 간격과 최대 조회 시간은 VTI와 연동 시험 전 확정한다. API 서버의 가동 확인(`/health`)만으로 분석 워커의 가동은 확인되지 않는다.

## 5. INT-001: 결과 JSON과 후보 PNG 조회

`GET /api/v1/analysis-jobs/{job_id}/results` → **HTTP 200** (`SUCCEEDED`일 때). 미완료 또는 실패 작업은 **409 `RESULT_NOT_READY`**이며 실패 원인은 INT-004의 `error`에서 확인한다.

결과 JSON의 전체 필드와 타입은 [결과 JSON Schema v0](schemas/analysis-result-v0.schema.json)에 정의되어 있다. [배치 결과 예시](examples/analysis-result-v0.example.json)는 필드 구조를 확인하기 위한 참고자료이며, 실제 INT-001 응답에서는 아래 설명처럼 PNG 경로가 API 경로로 바뀐다. 상위 필드는 `schema_version`(현재 `0.1.0`), `job_id`, `monitoring_data_id`, `status`, `success`, `completed_at`, `processing`, `candidate_count`, `candidates`, `warnings`, `error`다. 후보에는 `candidate_id`, `rank`, 대표 프레임·시각·GPS, ROI·후보 영역, 온도 통계, 탐지 근거, `result_image`가 포함된다. GPS와 촬영시각은 없으면 `null`일 수 있다. 후보가 없으면 `candidate_count: 0`, `candidates: []`다.

새로 분석한 후보에는 대표 프레임의 `roi_temperature_c`가 포함된다. `minimum`, `mean`, `maximum`은 **ATT의 분석 ROI 전체 픽셀 온도(°C)**의 최저·평균·최고이고, `max_minus_min`과 `max_minus_mean`은 각각 최고−최저, 최고−평균이다. 기존 `temperature_c`는 **후보 사각형**의 온도와 주변 배경 대비값이므로 구분한다. ATG는 촬영시각·GPS에 사용한다. 기존에 저장된 결과에는 `roi_temperature_c`가 없을 수 있다.

실제 INT-001 응답은 저장된 결과의 `result_image.path`를 다음처럼 API 상대 경로로 바꿔 반환한다. 배치 결과 예시 파일의 `images/...png`는 **배치 산출물 내부 경로**이므로 API 응답 경로로 사용하지 않는다.

```json
{
  "candidate_id": "JOB-20261102-000001-CAND-0001",
  "result_image": {
    "format": "PNG",
    "width": 384,
    "height": 288,
    "path": "/api/v1/analysis-jobs/JOB-20261102-000001/images/JOB-20261102-000001-CAND-0001",
    "grid": {"rows": 2, "columns": 3}
  }
}
```

위 JSON 조각은 후보 객체의 **일부**다. PNG 크기는 원본 열화상 해상도를 따르며 384×288은 현재 대표 입력 예시다. PNG에는 전체 화면의 2×3 격자(흰색), 실제 분석 ROI(빨간색), 후보 영역(초록색)을 표시한다. VTI 백엔드는 `https://api.daon.example`과 `result_image.path`를 결합해 `GET` 요청을 보내고 같은 Bearer 헤더를 붙인다. 성공 응답은 `Content-Type: image/png`다. 문서용 고정 샘플 작업 `JOB-20261030-000001`은 JSON·상태 예시만 있으며 PNG는 404를 반환한다.

## 6. HTTP 오류와 작업 실패

API 오류 형식:

```json
{"error":{"code":"RESULT_NOT_READY","message":"Analysis result is not ready"}}
```

| HTTP | 코드 | 적용 상황 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | Bearer 키 누락·불일치 |
| 404 | `JOB_NOT_FOUND` | 존재하지 않는 작업 ID |
| 404 | `IMAGE_NOT_FOUND` | 해당 후보 PNG가 없거나 문서용 샘플 PNG 조회 |
| 409 | `RESULT_NOT_READY` | 아직 성공하지 않은 작업의 결과·PNG 요청 |
| 422 | `INVALID_REQUEST` | INT-003 JSON 필드 형식·검증 오류 |
| 503 | `AUTH_NOT_CONFIGURED` | 서버에 API 키 미설정 |
| 503 | `RESULT_STORAGE_ERROR` | 완료 작업의 결과 JSON·PNG 읽기 실패 |

다운로드·파일 크기/체크섬 불일치·분석 실패 같은 **비동기 작업 오류는 POST의 HTTP 오류가 아니라 INT-004의 `status: FAILED`, `error.code/message`**로 전달된다. 현재 발생 가능한 예시는 `DOWNLOAD_FAILED`, `SOURCE_URL_NOT_ALLOWED`, `FILE_INTEGRITY_ERROR`, `INPUT_FILE_NOT_FOUND`, `ANALYSIS_FAILED`, `WORKER_INTERRUPTED`다. 자동 재시도 대상과 횟수는 [작업 상태 문서](JOB_STATE_V0.md)에 적었다.

## 7. 현재 검증과 연동 시험 전 확정 사항

현재 FastAPI·Bearer·SQLite 상태/진행률·단일 분석 워커·결과 JSON/PNG 조회가 구현되어 있다. 2026-11-02 실제 ATT·ATG의 첫 30프레임을 로컬 파일 참조로 접수부터 PNG 조회까지 실행했고 50개 후보와 임시 파일 정리를 확인했다. 이는 **VTI Presigned URL/실제 네트워크·전체 프레임·VTI 통합 연동을 검증한 결과는 아니다**. 근거는 [실데이터 검증 기록](REAL_DATA_SMOKE_2026-11-02.md)이다.

양측 확정이 필요한 항목은 ATT·ATG URL의 발급 주체·허용 호스트·유효기간, 배포 HTTPS 기준 주소와 접근망, Bearer 키 전달·교체, 폴링 주기·최대 대기 시간, `job_id` 중복 요청 정책, 재분석 시 후보 ID 매핑, 결과 보관·삭제 기간이다. 작업별 준비·검증 상태는 [VTI 연동 시험 체크리스트](VTI_INTEGRATION_TEST_CHECKLIST.md)에 기록한다.
