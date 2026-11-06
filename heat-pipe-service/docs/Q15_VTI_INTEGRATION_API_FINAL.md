# Q15 VTI–DaonH&S 연동 API 명세 2차 검토 반영본

- 작성일: 2026-11-06
- 버전: v1.0. VTI 1차 검토 의견 30건과 2차 검토 의견에 대한 DaonH&S 회신 및 현재 구현을 반영했다.
- 상태: **2차 검토의견 반영 연동 API 명세 확정본**. 아래 API 요청·응답 형식과 현재 처리 정책을 연동 기준으로 제시한다. `max_results=50`의 선정 품질 검증, 24시간 Presigned URL의 VTI 발급 설정, 장비 시각·GPS 등은 7장의 후속 확인 항목으로 관리한다. 시험 API는 2026-11-06에 배포하고 DaonH&S 측 공개 HTTPS 호출을 확인했다. VTI 시험망 접속과 VTI MinIO 실파일 연동은 아직 검증하지 않았다.
- 기준: [INT-001·003·004 개발 범위](INT_001_003_004_DEVELOPMENT_SCOPE.md), [OpenAPI 사본](openapi.json), [결과 JSON Schema v0](schemas/analysis-result-v0.schema.json)
- 범위: DaonH&S 담당 INT-003 분석 요청, INT-004 상태·진행률 조회와 작업 취소, INT-001 결과·PNG 조회. INT-002·005는 VTI 담당으로 이 문서의 범위 밖이다.

## 1. 연동 방향과 역할

```text
VTI: ATT·ATG를 MinIO에 업로드
  → VTI 백엔드: 파일 참조와 monitoring_data_id를 INT-003에 전송
  ← DaonH&S: HTTP 202 접수 확인(ACK)과 job_id 반환
  → VTI 백엔드: INT-004 상태·진행률 조회
  → SUCCEEDED 후 INT-001 결과 JSON 및 후보 PNG 조회
  → VTI: 후보 ID를 유지하고 관로 매칭·최종 판정·화면 표시
```

**파일 전송 방식과 URL 유효기간:** VTI가 ATT·ATG를 자체 MinIO에 업로드하고 INT-003 요청 직전에 각각의 **Presigned GET URL**을 발급해 보낸다. ATT 본문은 API에 직접 업로드하지 않는다. DaonH&S 분석 워커가 URL에서 파일을 내려받는다. 이 방식에는 DaonH&S의 VTI MinIO 계정이 필요하지 않다. `object_key`도 구현되어 있으나 공유 로컬 저장소 또는 MinIO 읽기 권한이 따로 필요하므로 연동 기본안은 URL이다. 서로 다른 서버 간 시험에서는 `HEATPIPE_ALLOWED_SOURCE_HOSTS`에 등록한 호스트의 HTTPS만 허용한다. 최종 동일 서버 배포에서는 별도의 `HEATPIPE_ALLOWED_INTERNAL_HTTP_HOSTS`에 등록한 Docker 내부 MinIO 호스트에 한해 HTTP를 허용한다. 두 방식 모두 리다이렉트는 허용하지 않는다. DaonH&S는 대기·실행·자동 재시도의 재다운로드를 고려해 **발급 시점부터 24시간 유효한 URL**을 연동 기준으로 요청한다. 실제 URL의 만료시간은 VTI 발급 설정으로 결정되므로 연동 시험 전에 24시간 적용 여부를 확인한다.

**R2-13 배포·파일명 정책:** VTI가 시험용 Presigned URL 호스트를 전달하면 연동 시험 전 API·워커 공통 `.env`의 `HEATPIPE_ALLOWED_SOURCE_HOSTS`에 호스트명만 등록하고 컨테이너를 재생성한다. 최종 동일 서버 배포에서는 포털 백엔드→분석 API에 Docker 내부 HTTP 서비스 주소를 사용한다. 분석 워커→MinIO도 공유 Docker 내부망의 HTTP를 사용하되, VTI MinIO 서비스 호스트명만 `HEATPIPE_ALLOWED_INTERNAL_HTTP_HOSTS`에 명시적으로 등록한다. 브라우저에서 분석 API나 MinIO를 직접 호출하지 않는다. VTI의 ATT·ATG Object Key와 원본 파일명은 서로 달라도 된다. 워커는 각각을 `monitoring_data_id.att`와 `.atg`로 내부 저장한 뒤 분석하므로 기존의 같은 stem 검사는 내부 파일에만 적용된다. API·워커의 정상 오류 메시지에는 서명 URL을 넣지 않으며 예외 메시지의 URL 쿼리는 마스킹한다. 서명 URL 원문은 다운로드·재시도를 위해 SQLite의 요청 기록에 저장되므로 DB 접근은 제한해야 한다.

**Q-01 ATT 크기:** 초기 문서의 ‘약 770MB’는 추정치였다. 실제로 검증한 ATT 한 건은 **875,667,464 bytes(약 875.7 MB, 835.1 MiB)**이고, 요청 예시의 `size_bytes`는 이 파일의 정확한 크기다. 확인된 데이터가 한 건이므로 회차당 **최대 크기는 아직 확정할 수 없다**. 현재 분석 API에 고정된 파일 크기 상한도 없다. VTI는 일단 이 실측 크기와 여유 공간을 기준으로 다운로드·저장 용량을 산정하고, 추가 실데이터의 크기를 확인한 뒤 양측이 상한과 서버 용량을 확정한다. `size_bytes`는 VTI가 각 파일의 실제 바이트 수를 보낼 때 다운로드 무결성 검증에 사용한다.

**C-02 접속 경로(수정 제안):** VTI가 제안한 시험·최종 배포의 두 단계에 맞춰 접속 경로를 구분한다.

| 단계 | 포털 백엔드 → DaonH&S 분석 API | 분석 워커 → 포털 MinIO |
|---|---|---|
| VTI 임시 서버 연동 시험 | DaonH&S 시험 API 기준 주소는 `https://heatpipe.daonhns.com`(HTTPS 443)이다. DaonH&S 측 외부망 접속은 확인했고 VTI 시험망 접속은 확인 전이다. | VTI가 발급한 Presigned HTTPS GET URL의 호스트에 443으로 접근한다. 분석 워커 컨테이너의 일반 HTTPS 접속 시 외부 출발 IP는 `211.60.149.76`이다. 실제 VTI MinIO 경로는 확인 전이다. |
| DaonH&S 서버 최종 배포 | 포털 백엔드와 분석 API를 Docker 내부망으로 연결한다. 내부 서비스 주소는 양측 배포 구성을 맞춘 뒤 정한다. | 공유 Docker 내부망의 MinIO HTTP 서비스 주소를 사용한다. 해당 호스트를 내부 HTTP 허용 목록에 등록하고 워커 컨테이너에서 접근을 검증한다. |

최종 배포 시 이용자 → 포털은 DaonH&S 서버의 도메인과 HTTPS 443을 사용한다. VTI의 프런트엔드·백엔드·MinIO와 DaonH&S 분석 API·워커가 같은 서버에 배포될 예정이므로, 최종 구성에서 포털 백엔드 → 분석 API에 외부망 443 개방을 일률적으로 적용하지 않는다. 시험 API의 HTTPS 기준 주소는 위와 같고, 최종 포털 도메인과 내부 서비스 주소는 양측 배포 구성을 맞춘 뒤 확정한다. 시험 단계의 HTTPS URL과 최종 배포의 내부 HTTP URL은 각각 해당 호출 주체에서 실제로 접근 가능한지 확인해야 한다.

**결과 전달 방식:** DaonH&S가 결과 JSON·CSV·PNG를 자체 저장소에 보관하고 VTI 백엔드가 Bearer 인증으로 JSON과 PNG를 API에서 읽는다. VTI가 결과 저장소나 MinIO에 직접 접근할 필요는 없다. `result_image.path`는 API 기준 주소에 붙일 상대 경로다. VTI 프런트엔드에는 Bearer 키를 전달하지 않고 VTI 백엔드에서 PNG를 중계한다. CSV는 내부 저장 산출물이며 이 명세의 API 조회 대상은 아니다.

## 2. 공통 규칙

| 항목 | 연동 계약 및 현재 구현 |
|---|---|
| 기준 주소 | INT-001·003·004 시험 API는 `https://heatpipe.daonhns.com`(HTTPS 443)이다. 최종 동일 서버 배포에서는 포털 백엔드가 별도로 확정할 Docker 내부 서비스 주소로 분석 API를 호출한다. |
| 데이터 | JSON, UTF-8. 서버 생성 시각(`created_at`, `started_at`, `completed_at` 등)은 UTC ISO 8601이다. 후보의 `captured_at`은 ATG 시각을 KST로 해석한 ISO 8601 `+09:00`이다. |
| 인증 | INT-001·003·004, 작업 취소, PNG 조회에 `Authorization: Bearer <API_KEY>` 필수. VTI 백엔드와 DaonH&S 서버만 키를 보관한다. |
| 공개 경로 | 운영 환경은 `GET /health`, `GET /version`만 공개한다. 로컬 개발 모드에서만 `/docs`, `/redoc`, `/openapi.json`을 제공한다. |
| 오류 본문 | `{"error":{"code":"...","message":"..."}}` |
| 작업 ID | 현재 `JOB-YYYYMMDD-NNNNNN`. 새로운 분석 요청마다 발급하며 같은 `Idempotency-Key` 재전송은 기존 ID를 반환한다. |
| 중복 요청 방지 | INT-003에 `Idempotency-Key`를 보내면 동일 키와 동일한 `monitoring_data_id` 및 ATT·ATG 각각의 `size_bytes`·`sha256` 재전송은 기존 `job_id`를 반환한다. URL은 비교에서 제외한다. |
| 후보 ID | 현재 DaonH&S가 `JOB-YYYYMMDD-NNNNNN-CAND-NNNN` 생성. 재분석은 새 작업이므로 동일 후보 ID 유지가 보장되지 않는다. |

**재분석 시 후보 ID(VTI 제안 동의):** 포털은 이전 작업이 최종 `FAILED`일 때에만 사용자에게 재분석을 허용한다. 실패 작업의 결과·후보는 INT-001에서 제공하지 않으므로 이전 후보 ID와 새 후보 ID를 연결하지 않는다. 새 재분석 요청은 같은 `monitoring_data_id`를 사용할 수 있지만 **새 `Idempotency-Key`**를 사용해야 새 `job_id`가 생성된다. 기존 키 재전송은 실패한 기존 작업을 반환한다. 성공 작업의 재분석은 현재 포털 범위 밖이며, 이 제한은 VTI 포털 정책으로 적용한다. 분석 API 자체는 동일 회차의 새 키를 이용한 재요청을 차단하지 않는다.

**Bearer 키 전달·교체(VTI 제안 동의):** 실제 API 키는 메일 본문이나 명세서에 적지 않고 별도 보안 채널로 전달한다. 예를 들어 암호화 파일과 비밀번호를 서로 다른 채널로 보낸다. VTI는 키를 포털 백엔드의 서버 환경 설정에만 보관하고 소스 코드·문서·프런트엔드에는 넣지 않는다. DaonH&S API도 `HEATPIPE_API_TOKEN` 환경 변수에서 키를 읽는다. 현재 양측의 단일 키를 즉시 교체하는 방식이므로 교체 일시를 미리 협의하고, 새 키를 양측 환경에 적용한 뒤 포털 백엔드와 DaonH&S API 컨테이너를 같은 작업 시간대에 재기동·재생성한다. 교체 직후 양측에서 인증 호출을 확인한다. 키 교체 중 짧은 호출 실패가 발생할 수 있으며, 구·신 키를 동시에 허용하는 무중단 교체는 현재 구현되어 있지 않다.

**B-13 운영 문서 경로:** 로컬 개발 모드에서는 `/docs`, `/redoc`, `/openapi.json`을 사용할 수 있다. 운영 Docker Compose는 `HEATPIPE_ENV=production`으로 실행해 세 경로를 제공하지 않으며 요청 시 404를 반환한다. VTI에 전달할 OpenAPI는 [정적 사본](openapi.json)이다. `/health`와 `/version`은 운영 환경에서도 공개한다. 별도 분석 워커가 가동되어야 접수 작업이 진행된다.

`GET /health`는 인증 없이 HTTP 200을 반환하며, API 서버의 응답 상태와 분석 워커의 가동 신호를 함께 제공한다. 응답 예시:

```json
{
  "status": "ok",
  "worker_running": true,
  "worker_last_activity_at": "2026-11-04T01:00:00+00:00",
  "queued_job_count": 2
}
```

`status=ok`는 API가 응답하고 SQLite를 읽을 수 있다는 뜻이다. `worker_running`은 분석 워커의 최근 가동 신호가 30초 이내에 기록되고 정상 종료 표시가 없을 때 `true`다. 워커는 분석 중에도 약 5초마다 신호를 기록한다. `worker_last_activity_at`은 마지막 신호의 UTC ISO 8601 시각이며 기록이 한 번도 없으면 `null`이다. `queued_job_count`는 상태가 `QUEUED`인 작업 수로, 재시도 대기 작업도 포함한다. 워커가 갑자기 중단되면 마지막 신호가 30초 넘게 갱신되지 않아 `worker_running=false`가 된다. HTTP 200만 보지 말고 이 필드를 확인해야 분석 접수 후 처리 가능 여부를 판단할 수 있다. 가동 신호는 워커 프로세스의 생존 확인이며 개별 작업의 진행·성공은 INT-004로 확인한다.

## 3. INT-003: 분석 요청 및 접수 확인

`POST /api/v1/analysis-jobs` → **HTTP 202 Accepted**. 202는 작업 접수와 ID 발급을 뜻하며 분석 완료를 뜻하지 않는다.

VTI 백엔드는 **회차의 한 번의 분석 요청마다 고유한 `Idempotency-Key` 헤더**를 생성해 보낸다. 응답을 받지 못해 재전송할 때는 키와 요청 본문을 그대로 사용한다. 키는 1~255자의 공백 없는 출력 가능한 ASCII 문자열이다. 요청 모델과 인증 검증은 재전송에도 동일하게 적용된다.

| 요청 필드 | 필수 | 형식·의미 |
|---|---:|---|
| `monitoring_data_id` | 예 | VTI의 회차/모니터링 데이터 식별자. **1~128자**, 영문·숫자·`_`·`-`만 허용. 초과 시 INT-003이 422 `INVALID_REQUEST`를 반환한다. |
| `att`, `atg` | 예 | 파일별 참조 객체. 각 객체에서 `url` 또는 `object_key` **정확히 하나**를 지정. |
| `att.url`, `atg.url` | 기본안 | 시험 서버에서는 HTTPS GET URL. 최종 동일 서버 배포에서는 명시적으로 허용한 Docker 내부 MinIO 호스트의 HTTP URL도 가능하다. 접수·다운로드 시 스킴과 호스트를 재검사하고 리다이렉트를 따르지 않는다. |
| `att.object_key`, `atg.object_key` | 대안 | 서버가 접근 가능한 저장소의 상대 Object Key. URL과 동시 지정 불가. |
| `size_bytes` | 아니요 | 양의 정수. 내려받은 파일 크기 검증. |
| `sha256` | 아니요 | 64자리 16진수. 내려받은 파일의 SHA-256 검증. |
| `frame_range.start` | 아니요 | 0부터 시작하는 프레임 인덱스. 기본 0. |
| `frame_range.end_exclusive` | 아니요 | 끝 프레임을 **포함하지 않는** 인덱스. 생략 시 끝까지 분석. `start`보다 커야 한다. |
| `analysis_roi` | 아니요 | 픽셀 좌표 `{x,y,width,height}`. 좌상단 원점, `x/y ≥ 0`, `width/height ≥ 1`. 생략 시 전체 화면의 2행×3열 격자 중 하단 가운데 칸을 분석한다(384×288 입력은 `x=128,y=144,width=128,height=144`). 명시적 ROI도 그 칸 내부여야 하며, 밖이면 작업이 `FAILED`로 끝난다. |

**Q-02 운영 기본 분석 범위:** VTI 포털은 운영 요청에서 `frame_range`와 `analysis_roi`를 **항상 생략한다**. 포털 사용자에게 분석 범위 선택 기능을 제공할 필요도 없다. 이 경우 ATT의 첫 프레임부터 마지막 프레임까지 프레임을 건너뛰지 않고 처리하며, 각 프레임의 공간 범위는 원본 영상을 2행×3열로 나눈 **하단 가운데 칸(2행 2열)**이다. 검증한 384×288 입력에서는 `{x:128,y:144,width:128,height:144}`로 고정된다. 프레임 해상도가 달라지면 같은 격자 규칙으로 좌표가 계산되며, 회차별로 임의 이동하지 않는다. 이전 `x:0,y:158,width:384,height:130`은 기본 ROI 변경 전의 값이므로 운영 기본값에 적용하지 않는다. 두 선택 필드는 DaonH&S의 개발·내부 시험을 위해 API에 남겨 둔다. 아래 요청 예시는 포털의 운영 요청 형태다.

**B-07 접수 시 URL 검증:** URL 형식은 요청 모델에서 검사하고, 시험 단계의 HTTPS 허용 호스트 또는 최종 동일 서버 배포의 명시적 내부 HTTP MinIO 호스트인지 INT-003 접수 전에 검사한다. 위반 시 작업을 만들지 않고 HTTP 422 `SOURCE_URL_NOT_ALLOWED`를 반환한다. 이 검사는 네트워크 접속·서명 유효기간·파일 내용까지 확인하지 않는다. INT-003이 202를 반환한 뒤에도 실제 다운로드나 파일 무결성 검증에 실패하면 INT-004가 `FAILED`를 반환할 수 있다. 분석 워커는 다운로드 직전 같은 스킴·허용 호스트 검사를 다시 수행한다.

예시 요청(주소·토큰·서명은 자리표시자):

```http
POST /api/v1/analysis-jobs HTTP/1.1
Authorization: Bearer <API_KEY>
Idempotency-Key: vti-20260813_132634-analysis-1
Content-Type: application/json

{
  "monitoring_data_id": "20260813_132634",
  "att": {"url": "https://minio.vti.example/inputs/20260813_132634.att?signature=...", "size_bytes": 875667464},
  "atg": {"url": "https://minio.vti.example/inputs/20260813_132634.atg?signature=...", "size_bytes": 151552}
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

`Idempotency-Key`는 SQLite 작업 레코드에 저장되므로 API 재시작 후에도 유지된다. 비교 기준은 `monitoring_data_id`와 **ATT·ATG 각각의** `size_bytes`·`sha256`이다. URL과 Object Key는 비교하지 않으며 SHA-256은 대소문자를 구분하지 않는다. 같은 키·같은 비교값으로 다시 POST하면 새 작업 없이 기존 `job_id`·`created_at`과 **현재 상태**를 HTTP 202로 반환한다. 새 Presigned URL을 보낸 경우 작업이 아직 `QUEUED`이면 저장된 다운로드 URL도 갱신한다. 이미 실행 중이거나 완료·실패한 작업은 URL을 바꾸지 않고 기존 작업을 반환한다. 비교값이 다르면 HTTP 409 `IDEMPOTENCY_KEY_CONFLICT`다. 키를 생략하면 매 POST마다 새 작업이 생성된다. `size_bytes`·`sha256`을 생략할 수는 있으나, 동일 입력을 안전하게 구분하려면 ATT와 ATG 모두에 제공해야 한다. 분석 범위나 ROI를 변경하여 새 분석을 원하면 새 키를 사용한다. 키는 작업 레코드가 SQLite에 남아 있는 동안 유지되며 현재 자동 만료·삭제 정책은 없다.

**C-05 응답 유실 시 접수 확인(M-01 적용):** VTI는 INT-003 응답을 받지 못했을 때 **최초 `Idempotency-Key`와 동일한 `monitoring_data_id`, ATT·ATG의 `size_bytes`·`sha256`**로 재전송하면 기존 작업의 `job_id`를 받을 수 있다. Presigned URL은 새로 발급해도 된다. `QUEUED` 상태에서는 새 URL로 갱신하고, 실행 중에는 최초 또는 마지막 대기 중에 저장한 URL을 유지한다. 키 또는 비교값을 잃은 경우에는 이 방식으로 접수 여부를 판단할 수 없으므로 운영자 확인 절차를 사용한다. VTI 연동 시험에서 응답 유실·URL 재발급·서버 재기동 후 재전송을 검증한다. 현재 `monitoring_data_id`로 작업을 조회하는 공개 API는 없다.

## 4. INT-004: 작업 상태·진행률·실패 조회

`GET /api/v1/analysis-jobs/{job_id}` → **HTTP 200**.

| 응답 필드 | 의미 |
|---|---|
| `job_id`, `monitoring_data_id` | 작업과 VTI 회차 식별자 |
| `status` | `QUEUED`, `VALIDATING`, `DOWNLOADING`, `ANALYZING`, `RENDERING`, `UPLOADING`, `SUCCEEDED`, `FAILED`, `CANCELLED` 중 하나 |
| `attempt`, `max_attempts` | 현재 시도 번호와 최대 시도 횟수. 최초 요청은 `1`/`3`; 재시도 대기 중에는 다음 시도 번호를 반환한다. |
| `cancel_requested` | 취소 요청 접수 여부. 실행 중 `true`이면 분석 워커가 중단 지점에서 `CANCELLED`로 전환한다. |
| `queue_position` | `QUEUED`일 때 앞에 대기 중인 작업 수(0부터). 그 외 상태는 `null`. 현재 실행 중인 작업은 포함하지 않는다. |
| `progress_percent` | 정수 0~100. 처리 단계 표시값이며 남은 시간 예측치는 아니다. |
| `processed_frame_count` | 유효하게 분석한 프레임 수. 빈 프레임은 제외. |
| `total_frame_count` | 선택된 프레임 구간의 전체 수. 분석 전에는 `null`일 수 있다. |
| `status_message` | 현재 단계 설명 |
| `created_at`, `started_at`, `completed_at` | 생성·시작·종료 시각. 미도달 단계는 `null`. |
| `error` | 작업 실패 시 `{code,message,retryable}`. 그 외에는 `null`. `retryable`은 재시도 대상 오류 유형 여부이며 성공 보장은 아니다. |

초기 상태는 `QUEUED`·0%다. 현재 단계값은 대략 검증 1%, 다운로드 5%, 분석 10~85%, 렌더링 90%, 결과 저장 95%, 성공 100%다. 자동 재시도 때 같은 `job_id`가 `QUEUED`·0%로 돌아갈 수 있으며, `attempt`가 증가하므로 포털은 새 시도임을 표시할 수 있다. 최대 3회 시도하며 다운로드·업로드·입출력 오류 및 실행 중단 등 일부 오류만 재시도한다. 최종 `FAILED`이면 `error`를 확인한다. 상세 정책은 [작업 상태 문서](JOB_STATE_V0.md)를 참조한다.

**B-09 대기 순번:** `queue_position`은 조회 시점에 SQLite 대기열에서 해당 작업보다 먼저 접수되어 아직 `QUEUED`인 작업의 수다. 취소·작업 선점·재시도에 따라 변할 수 있다. 재시도 지연 중인 작업도 대기 작업으로 센다. 분석 워커는 재시도 지연이 끝나지 않은 작업을 일시적으로 건너뛰므로, 이 값은 정확한 시작 순서나 예상 대기 시간을 보장하지 않는다. 포털은 ‘앞에 대기 중인 작업 N건’으로 표시하고 완료 예상 시각으로 환산하지 않는다.

**B-05 재실행 가능 오류 표시:** `error.retryable=true`는 오류 코드가 자동 재시도 대상 유형임을 뜻한다. `attempt == max_attempts`인 최종 실패에서도 유형이 같으면 `true`이며, 해당 작업의 자동 재시도는 더 이상 진행되지 않는다. VTI 포털은 `attempt`·`max_attempts`와 재조회 상태를 함께 확인해 자동 재시도 대기와 최종 실패를 구분한다. `false`인 입력·무결성 오류는 입력이나 파일 참조를 수정해야 한다. 이 값은 새 분석 요청의 성공을 보장하지 않는다.

**B-06 재시도 시 `started_at`:** 최초 접수의 `QUEUED`에서는 `started_at=null`이다. 분석 워커가 작업을 선점해 `VALIDATING`으로 바꿀 때 현재 UTC 시각을 기록한다. 자동 재시도를 위해 같은 작업을 다시 `QUEUED`로 돌릴 때 `started_at=null`로 초기화하고, 다음 시도를 선점하면 **새 시도의 시작 시각**으로 다시 기록한다. `created_at`과 `job_id`는 유지된다. 포털의 실행 3시간 한도는 현재 실행 시도의 `started_at`부터 계산하며 재시도 대기(`QUEUED`) 중에는 이전 시작 시각을 사용하지 않는다.

예시 응답:

```json
{
  "job_id": "JOB-20261102-000001",
  "monitoring_data_id": "20260813_132634",
  "status": "ANALYZING",
  "attempt": 1,
  "max_attempts": 3,
  "cancel_requested": false,
  "queue_position": null,
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

**분석 완료 확인 및 폴링(VTI 제안에 대한 수정 의견):** 콜백 없이 VTI가 이 경로를 **10초마다** 조회한다. 현재 예상 시험 규모에서는 이 간격을 수용할 수 있다. `SUCCEEDED`일 때 INT-001을 호출하고 `FAILED`일 때 오류를 표시한다. 포털의 `QUEUED` 6시간·실행 3시간 대기 한도는 일단 임시 운영값으로 적용하고, VTI MinIO 및 시험 서버에서 전체 파일을 실행한 결과로 재조정한다. 대기 한도를 넘으면 포털에는 **처리 지연·상태 재확인 필요**로 안내하고 동일한 `job_id`로 나중에 다시 조회할 수 있게 한다. 시간 초과만으로 DaonH&S 작업 상태가 `FAILED`로 전환되지는 않는다. 현재 분석 워커에는 6시간·3시간 강제 중단 기능이 없으며, 실제 실패 여부는 INT-004의 `status=FAILED`로 판단한다. 자동 재시도 시 같은 작업이 `QUEUED`로 돌아가고 `started_at`이 초기화될 수 있으므로, 포털은 조회 중 관측한 상태 전환을 반영해 대기 시간을 계산해야 한다. `/health`의 `worker_running`으로 분석 워커 가동 여부를 확인할 수 있지만 개별 작업 결과는 INT-004로 판단한다.

**M-03 작업 취소:** `POST /api/v1/analysis-jobs/{job_id}/cancel`은 같은 Bearer 인증을 사용하고 HTTP 202로 INT-004 상태 객체를 반환한다. `QUEUED` 작업은 즉시 `CANCELLED`가 된다. 실행 중에는 `cancel_requested=true`로 응답하고, 분석 워커가 다운로드·프레임 분석·결과 저장의 중단 지점에 도달하면 임시 파일과 이미 업로드한 결과를 정리한 뒤 `CANCELLED`로 바꾼다. `CANCELLED` 작업을 다시 취소해도 같은 상태를 반환한다. 이미 `SUCCEEDED` 또는 최종 `FAILED`인 작업은 HTTP 409 `CANCEL_NOT_ALLOWED`다. 취소된 작업은 자동 재시도하지 않으며 INT-001 결과 조회는 409 `RESULT_NOT_READY`다. 분석 워커가 중단되었다가 다시 시작해도 접수된 취소 요청은 재실행하지 않는다. 현재 진행 중인 개별 저장소 호출이 끝나야 취소가 반영될 수 있으므로, HTTP 202 직후 `CANCELLED`를 가정하지 말고 INT-004로 확인해야 한다. 포털의 시간 초과 자체가 취소 요청을 자동 전송하지는 않는다.

## 5. INT-001: 결과 JSON과 후보 PNG 조회

`GET /api/v1/analysis-jobs/{job_id}/results` → **HTTP 200** (`SUCCEEDED`일 때). 미완료 또는 실패 작업은 **409 `RESULT_NOT_READY`**이며 실패 원인은 INT-004의 `error`에서 확인한다.

결과 JSON의 전체 필드와 타입은 [결과 JSON Schema v0](schemas/analysis-result-v0.schema.json)에 정의되어 있다. [문서용 API 예시](examples/analysis-result-v0.example.json)와 [실제 전체 프레임 API 응답 형태 샘플](examples/int-001-real-result-20261102.json)에서 필드 구조를 확인할 수 있다. 상위 필드는 `schema_version`(현재 `0.3.0`), `job_id`, `monitoring_data_id`, `status`, `success`, `completed_at`, `processing`, `detected_candidate_count`, `candidate_count`, `candidates`, `warnings`, `error`다. R2-02에 따라 `processing.parameters.max_results`와 상한 전 적격 후보 트랙 수 `detected_candidate_count`를 추가했다. 새 결과에서 `detected_candidate_count > candidate_count`이면 결과가 상한으로 잘린 것이다. 기존 저장 결과에서 상한 전 수를 알 수 없으면 `detected_candidate_count: null`이다. API 서버 자체 버전과는 별개다. 후보에는 `candidate_id`, `rank`, 대표 프레임·시각·GPS, ROI·후보 영역, 온도 통계, 탐지 근거, `result_image`가 포함된다. GPS와 촬영시각은 없으면 `null`일 수 있다. 후보가 없으면 `candidate_count: 0`, `candidates: []`다.

**Q-03 촬영 시각·GPS:** `candidates[].captured_at`은 후보 **대표 프레임**에 대응하는 ATG 시각이다. ATG 원본에는 시간대 오프셋이 없어 현재는 장비 시각을 KST로 가정해 `2026-08-13T13:30:53+09:00` 형태로 반환한다. 이는 UTC 변환값이 아니며, 실제 장비의 시간대 설정은 DaonH&S가 확인해야 한다. 후보 대표 프레임 번호에 대응하는 ATG 레코드가 없으면 `captured_at`과 `gps`가 함께 `null`이고 결과 `warnings`에 `ATT_ATG_COUNT_MISMATCH`가 기록된다. ATG 레코드가 있으면 현재 구현은 위도·경도 숫자를 그대로 반환하며 **GPS 신호 없음만을 판별해 `gps=null`로 바꾸는 기능은 없다**. 무신호 표기 방식은 실제 ATG 샘플·장비 명세로 확인해야 한다. `captured_at`은 후보마다 다르거나 `null`일 수 있으므로 포털의 회차명 생성 기준으로 사용하지 않는다. 회차 식별자 `monitoring_data_id`는 VTI가 INT-003 요청에서 전달한다.

**Q-04 결과 경고:** 성공 결과의 `warnings`는 `[{"code":"...","message":"..."}]` 형식이며 경고가 없으면 `[]`이다. 현재 생성하는 경고는 아래 두 종류다.

| `code` | 발생 조건 | `message` 예시 |
|---|---|---|
| `ATT_ATG_COUNT_MISMATCH` | ATT 전체 프레임 수와 ATG 레코드 수가 다름 | `ATT frames (3959) and ATG records (3885) differ; unmatched candidate metadata is null.` (실제 전체 파일 결과) |
| `EMPTY_THERMAL_FRAMES_SKIPPED` | 온도 값이 비정상적이거나 전체 온도가 0 이하인 열화상 프레임을 건너뜀 | `Skipped 2 empty or invalid thermal frame(s).` (형식 예시) |

두 경고는 결과의 데이터 완전성을 알려 주며 작업을 실패로 바꾸지 않는다. 포털은 결과 상세에서 운영자가 확인할 수 있는 안내로 표시하고, 일반 실패 메시지로 처리하지 않는다. 화면 문구는 `code`로 한국어 문장을 매핑할 수 있다. ATG 불일치로 후보의 `gps`가 `null`이면 해당 후보의 지도 위치를 임의로 만들지 않는다. B-03은 후보 PNG가 하나라도 생성·저장·검증에 실패하면 작업 **전체를 `FAILED`**로 처리하므로, PNG 누락 후보를 제외하고 `warnings`에 남기는 경고 코드는 없다.

앞으로 INT-003으로 접수되는 작업의 모든 후보에는 대표 프레임의 `roi_temperature_c` 객체가 **필수**다. 한 후보라도 누락되거나 null이면 작업을 성공으로 처리하지 않는다. `minimum`, `mean`, `maximum`은 **ATT의 분석 ROI 전체 픽셀 온도(°C)**의 최저·평균·최고이고, `max_minus_min`과 `max_minus_mean`은 각각 최고−최저, 최고−평균이다. 기존 `temperature_c`는 **후보 사각형**의 온도와 주변 배경 대비값이므로 구분한다. ATG는 촬영시각·GPS에 사용한다. 이전에 저장된 결과에서 계산되지 않은 `roi_temperature_c`는 null로 제공될 수 있다.

INT-001 응답은 저장된 결과의 `result_image.path`를 다음처럼 API 상대 경로로 바꿔 반환한다. 배치 산출물 내부의 `images/...png`는 API 응답 경로로 사용하지 않는다.

```json
{
  "candidate_id": "JOB-20261102-000007-CAND-0001",
  "result_image": {
    "format": "PNG",
    "width": 384,
    "height": 288,
    "path": "/api/v1/analysis-jobs/JOB-20261102-000007/images/JOB-20261102-000007-CAND-0001",
    "sha256": "99a4bc4e97cc9197b8552b65b4e9bcb529bbe1b5aced7e234b7a7b2d871126ae",
    "size_bytes": 61026,
    "grid": {"rows": 2, "columns": 3}
  }
}
```

위 JSON 조각은 후보 객체의 **일부**다. PNG 크기는 원본 열화상 해상도를 따르며 384×288은 현재 대표 입력 예시다. PNG에는 전체 화면의 2×3 격자(흰색), 실제 분석 ROI(빨간색), 후보 영역(초록색)을 표시한다. VTI 백엔드는 시험 기준 주소 `https://heatpipe.daonhns.com`과 `result_image.path`를 결합해 `GET` 요청을 보내고 같은 Bearer 헤더를 붙인다. 성공 응답은 `Content-Type: image/png`다. 문서용 고정 샘플 작업 `JOB-20261030-000001`에도 `MOCK` 표시가 있는 샘플 PNG를 제공한다.

**B-08 PNG 무결성:** `result_image.sha256`과 `size_bytes`는 PNG 생성 시 실제 파일 바이트로 계산한다. 분석 워커는 업로드한 PNG를 다시 읽어 크기·해시·원본 바이트가 일치해야 작업을 `SUCCEEDED`로 전환한다. INT-001은 두 값을 항상 반환하며, 이전에 저장된 결과에 값이 없으면 저장된 PNG에서 계산해 채운다. VTI는 PNG GET 응답 본문의 바이트 수와 SHA-256을 이 값과 비교해 장기 보관본의 손상 여부를 확인할 수 있다.

**B-03 처리 방식:** 분석 워커는 모든 후보 PNG를 저장한 뒤 저장소에서 다시 읽어 원본 PNG와 일치하는지 확인한다. 한 장이라도 생성·저장·재조회에 실패하면 작업을 `SUCCEEDED`로 표시하지 않는다. 재시도 가능한 입출력·업로드 실패는 기존 정책에 따라 최대 3회 시도하고, 복구되지 않으면 작업 **전체가 `FAILED`**가 된다. 해당 작업의 결과는 INT-001에서 제공하지 않고 실패 원인은 INT-004의 `error`에서 확인한다. 완료 후 보관 중 저장소 장애가 발생하면 기존 성공 상태가 바뀌지는 않으며 PNG 조회는 503 `RESULT_STORAGE_ERROR`를 반환한다.

**C-07 결과 보관(VTI 제안 동의):** `SUCCEEDED` 작업의 `completed_at`부터 결과 JSON과 모든 후보 PNG를 **최소 7일** 보관해 INT-001 및 PNG API로 재조회할 수 있게 한다. VTI 포털은 성공 확인 후 결과를 자체 저장소에 복사한다. 현재 DaonH&S 분석 서비스는 결과 파일을 영속 Docker 볼륨에 저장하며 자동 삭제 기능은 없다. 7일 이후 삭제 시점은 시험 서버 용량과 운영 정책을 확인해 DaonH&S가 정한다. 배포·유지보수 시 7일 이내 결과 볼륨을 삭제하지 않고 디스크 사용량을 확인해야 한다. 저장소 장애 또는 데이터 손실 시 API가 503을 반환할 수 있으므로 실제 서버의 볼륨·용량·복구 절차를 배포 검증에 포함한다.

## 6. HTTP 오류와 작업 실패

API 오류 형식:

```json
{"error":{"code":"RESULT_NOT_READY","message":"Analysis result is not ready"}}
```

| HTTP | 코드 | 적용 상황 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | Bearer 키 누락·불일치 |
| 404 | `JOB_NOT_FOUND` | 존재하지 않는 작업 ID |
| 404 | `IMAGE_NOT_FOUND` | 결과에 없는 후보 ID의 PNG 조회 |
| 409 | `RESULT_NOT_READY` | 아직 성공하지 않은 작업의 결과·PNG 요청 |
| 409 | `IDEMPOTENCY_KEY_CONFLICT` | 같은 `Idempotency-Key`를 다른 분석 요청에 재사용 |
| 409 | `CANCEL_NOT_ALLOWED` | 이미 성공했거나 최종 실패한 작업의 취소 요청 |
| 422 | `SOURCE_URL_NOT_ALLOWED` | ATT·ATG URL의 스킴·호스트가 배포 환경의 허용 목록에 없음(작업 미접수) |
| 422 | `INVALID_REQUEST` | INT-003 JSON 필드 형식·검증 오류 |
| 503 | `AUTH_NOT_CONFIGURED` | 서버에 API 키 미설정 |
| 503 | `RESULT_STORAGE_ERROR` | 완료 작업의 결과 JSON·PNG 읽기 실패 |

다운로드·파일 크기/체크섬 불일치·분석 실패 같은 **비동기 작업 오류는 POST의 HTTP 오류가 아니라 INT-004의 `status: FAILED`, `error.code/message/retryable`**로 전달된다. HTTP·작업 오류 코드 전체 목록은 [R2-12 오류 코드표](VTI_R2_12_ERROR_CODES_2026-11-05.md)에 정리했다. 이미지 API도 성공 200만 `image/png`이고 오류는 `application/json`이다. GET 422 역시 `HTTPValidationError`가 아니라 `ErrorResponse`다. 자동 재시도 대상과 횟수는 [작업 상태 문서](JOB_STATE_V0.md)에 적었다.

## 7. 검증 결과와 후속 확인 항목

현재 FastAPI·Bearer·SQLite 상태/진행률·단일 분석 워커·결과 JSON/PNG 조회가 구현되어 있다. 2026-11-02 실제 ATT·ATG 전체 3,959프레임을 로컬 Docker GPU 분석 워커에서 실행했고, 당시 설정인 `max_results=50`으로 후보·PNG 50건을 확인했다. 로컬 모의 서명 HTTPS URL 입력 작업의 배치 소요 시간은 91.362초, 저장 결과 JSON은 76,686바이트였다. **포털의 작업당 1,000건은 허용 한도이며 분석 결과의 확정 상한이 아니다.** 현재 `max_results=50`은 임시 설정이다. 적격 후보가 상한을 넘으면 전체 프레임 범위를 10개 구간으로 나누어 각 구간의 높은 점수 후보부터 번갈아 채우는 방식은 구현했으나, 실제 ATT·ATG로 재검증하기 전에는 UAT 정책으로 확정하지 않는다. 차량 열원은 별도의 판별 근거가 없어 자동 제외하지 않는다. 이전 50건 샘플과 시간·크기 측정값은 새 선정 방식의 검증 결과가 아니며, 상한 적용 전 후보 총수도 아니다. 새 정책의 실데이터 전 구간 재실행 및 PNG·저장 공간·처리 시간 검증이 남아 있다. 11월 6일 시험 서버의 공개 HTTPS 주소에서 합성 ATT·ATG 4프레임 작업을 INT-003 202로 접수하고 INT-004 `SUCCEEDED`·100%, INT-001 후보 1건과 PNG 크기·해시 일치를 확인했다. API·워커 재시작 후에도 상태와 결과 JSON이 유지됐다. 시험 서버의 전체 실파일 성능, VTI MinIO 접속 및 VTI 백엔드 통합 연동은 아직 검증되지 않았다. 기존 로컬 측정의 근거는 [HTTPS URL 로컬 검증](HTTPS_URL_LOCAL_TEST_2026-11-02.md)과 [CPU·GPU Docker 비교](CPU_GPU_DOCKER_COMPARISON_2026-11-02.md)다.

VTI에 전달할 시험 범위·측정값·한계의 독립적인 요약은 [T-05 실데이터 검증 기록](T05_REAL_DATA_VERIFICATION_FOR_VTI_2026-11-04.md)에 정리했다. 첫 30프레임 시험은 이전 ROI 기준이며 처리 시간 측정값이 없으므로, 포털의 시간 한도 산정에는 현재 ROI의 전체 프레임 시험 기록을 참고한다.

API 형식과 처리 정책은 본 문서와 [OpenAPI 정적 사본](openapi.json)을 기준으로 한다. M-01 중복 요청 방지는 `Idempotency-Key` 방식, M-02 분석 워커 가동 확인은 `/health` 필드, B-03 후보 PNG 제공은 성공 전 전체 재조회 검증 방식으로 구현했다. 포털은 실패 작업에만 재분석을 허용하고 이전·새 후보 ID를 연결하지 않는다. 성공 결과는 최소 7일 보관한다.

| 후속 확인 항목 | 현재 동작과 확인 계획 |
|---|---|
| `max_results` 상한·후보 선정 | 현재 작업당 반환 상한은 50건으로 적용한다. 차량 부근 반복 후보가 상한을 차지하지 않는지 실제 ATT·ATG 재실행으로 검증하고, 필요하면 선정 방식과 상한을 UAT 전에 조정·공유한다. 포털의 1,000건은 허용 한도다. |
| ROI의 130℃ 값 | 입력 ATT의 픽셀값이며 센서 측정 상한인지는 미확인이다. 장비 정보를 확인하기 전에는 이 값만으로 후보를 제외하지 않는다. 포화 여부 필드 추가는 측정 상한이 확인되면 별도 협의한다. |
| ATT·ATG 결합과 위치 오차 | ATT에 프레임별 시각이 없어 현재는 동일 인덱스로 ATG를 결합한다. 74건의 개수 차이와 장비 동기화 방식, 기준 위치 대비 오차를 확인한 뒤 시각 결합 및 포털의 5m 자동 귀속 기준을 재검토한다. |
| ATT·ATG Presigned URL 유효기간 | DaonH&S 연동 기준은 발급 시점부터 24시간이다. VTI가 발급하는 실제 URL에 24시간 설정이 적용되는지 연동 시험 전에 확인한다. |
| MinIO 호스트·분석 워커 출발지 주소 | VTI가 발급할 HTTPS URL 호스트를 DaonH&S 허용 목록에 등록한다. 워커 컨테이너의 일반 HTTPS 외부 출발 IP는 `211.60.149.76`으로 확인했다. VTI MinIO 실제 접속 시 경로와 허용 목록 적용을 다시 확인한다. |
| 시험 API 기준 주소·접근망 | INT-001·003·004의 시험 HTTPS 기준 주소는 `https://heatpipe.daonhns.com`이다. DaonH&S 측 외부망에서 인증서·작업 접수·상태·결과·PNG 조회를 확인했다. 11월 9일 제공 목표에 앞서 VTI 시험망의 443 접속과 인증 호출을 확인한다. INT-002·005는 VTI 담당으로 이 문서의 범위 밖이며, 전체 통합 연동 시험은 11월 16일부터 가능하도록 예정되어 있다. 최종 동일 서버 배포 때는 Docker 내부 서비스 주소를 별도로 확정한다. |
| Bearer 키 | 키 값과 전달 채널, 최초 적용 일시를 별도 협의한다. 키 자체는 문서에 싣지 않는다. |
| ATG 장비 시각·GPS 무신호 표기 | 현재 촬영 시각은 KST로 해석하고, GPS 무신호만 따로 판별하지 않는다. 실제 장비 설정·샘플로 확인한다. |
| ATT 최대 크기·대기 한도 | 확인한 ATT 한 건은 875,667,464 bytes다. 최대 크기는 추가 실데이터로 정한다. 포털은 10초 폴링과 대기 6시간·실행 3시간을 임시로 적용하고 연동 시험 측정값으로 재검토한다. |

작업별 준비·검증 상태는 [VTI 연동 시험 체크리스트](VTI_INTEGRATION_TEST_CHECKLIST.md)에 기록한다. 결과 JSON의 필드 경로·타입·단위·필수 여부·null 조건은 [필드 설명서](RESULT_JSON_SCHEMA_V0_FIELD_GUIDE.md)와 [결과 JSON Schema](schemas/analysis-result-v0.schema.json)에 정의한다. [실제 전체 프레임 결과 샘플](examples/int-001-real-result-20261102.json)은 기존 점수순 `max_results=50` 설정의 INT-001 응답 형식이며, 선정 방식과 상한을 확정한 뒤 새 결과 샘플로 교체한다.
