# Q15 VTI–DaonH&S 연동 API 명세 초안

- 작성일: 2026-11-02 · 수정일: 2026-11-04
- 버전: v0.3 검토본. VTI 회신 30건을 반영했으며, 시험 서버 주소·접근망과 URL 유효기간 등 양측 확정이 필요한 운영값은 별도로 표시한다.
- 상태: **VTI 사전 검토용 초안**. 2026-11-06 확정본 전에 전달하여 연동 방식과 미확정 항목에 대한 의견을 받기 위한 문서다. 현재 코드 동작과 `확정 필요` 제안을 구분한다.
- 기준: [INT-001·003·004 개발 범위](INT_001_003_004_DEVELOPMENT_SCOPE.md), [OpenAPI 사본](openapi.json), [결과 JSON Schema v0](schemas/analysis-result-v0.schema.json)
- 범위: INT-003 분석 요청, INT-004 상태·진행률 조회와 작업 취소, INT-001 결과·PNG 조회. GIS/SHP, INT-002·005는 제외한다.

## 1. 연동 방향과 역할

```text
VTI: ATT·ATG를 MinIO에 업로드
  → VTI 백엔드: 파일 참조와 monitoring_data_id를 INT-003에 전송
  ← DaonH&S: HTTP 202 접수 확인(ACK)과 job_id 반환
  → VTI 백엔드: INT-004 상태·진행률 조회
  → SUCCEEDED 후 INT-001 결과 JSON 및 후보 PNG 조회
  → VTI: 후보 ID를 유지하고 관로 매칭·최종 판정·화면 표시
```

**파일 전송 방식(VTI 동의, 유효기간 조정 제안):** VTI가 ATT·ATG를 자체 MinIO에 업로드하고 INT-003 요청 직전에 각각의 **Presigned HTTPS GET URL**을 발급해 보낸다. ATT 본문은 API에 직접 업로드하지 않는다. DaonH&S 분석 워커가 URL에서 파일을 내려받는다. 이 방식에는 DaonH&S의 VTI MinIO 계정이 필요하지 않다. `object_key`도 구현되어 있으나 공유 로컬 저장소 또는 MinIO 읽기 권한이 따로 필요하므로 연동 기본안은 URL이다. URL 호스트는 DaonH&S의 `HEATPIPE_ALLOWED_SOURCE_HOSTS`에 등록하며 HTTP와 리다이렉트는 허용되지 않는다. VTI 제안은 URL 발급 시점부터 **12시간**이지만, 현재 분석 워커는 자동 재시도 때 임시파일을 정리하고 두 파일을 다시 내려받는다. 대기·실행·재시도와 URL 발급부터 분석 요청까지의 시간을 고려해 **24시간**으로 수정 제안한다. 유효기간은 양측 회신 후 확정한다.

**Q-01 ATT 크기:** 초기 문서의 ‘약 770MB’는 추정치였다. 실제로 검증한 ATT 한 건은 **875,667,464 bytes(약 875.7 MB, 835.1 MiB)**이고, 요청 예시의 `size_bytes`는 이 파일의 정확한 크기다. 확인된 데이터가 한 건이므로 회차당 **최대 크기는 아직 확정할 수 없다**. 현재 분석 API에 고정된 파일 크기 상한도 없다. VTI는 일단 이 실측 크기와 여유 공간을 기준으로 다운로드·저장 용량을 산정하고, 추가 실데이터의 크기를 확인한 뒤 양측이 상한과 서버 용량을 확정한다. `size_bytes`는 VTI가 각 파일의 실제 바이트 수를 보낼 때 다운로드 무결성 검증에 사용한다.

**C-02 접속 경로(수정 제안):** VTI가 제안한 시험·최종 배포의 두 단계에 맞춰 접속 경로를 구분한다.

| 단계 | 포털 백엔드 → DaonH&S 분석 API | 분석 워커 → 포털 MinIO |
|---|---|---|
| VTI 임시 서버 연동 시험 | DaonH&S 시험 서버에 HTTPS 443 접속 경로가 준비되면 기준 주소를 공유한다. 현재 주소·접근망은 미정이다. | VTI가 발급한 Presigned HTTPS GET URL의 호스트에 443으로 접근한다. 분석 워커의 출발지 주소는 서버·네트워크 구성 후 확인한다. |
| DaonH&S 서버 최종 배포 | 포털 백엔드와 분석 API를 Docker 내부망으로 연결한다. 내부 서비스 주소는 양측 배포 구성을 맞춘 뒤 정한다. | 발급된 HTTPS URL의 호스트가 분석 워커 컨테이너에서 접근 가능하도록 MinIO의 도메인·프록시·내부 경로를 정하고 검증한다. |

최종 배포 시 이용자 → 포털은 DaonH&S 서버의 도메인과 HTTPS 443을 사용한다. VTI의 프런트엔드·백엔드·MinIO와 DaonH&S 분석 API·워커가 같은 서버에 배포될 예정이므로, 최종 구성에서 포털 백엔드 → 분석 API에 외부망 443 개방을 일률적으로 적용하지 않는다. 시험 API의 HTTPS 기준 주소, 최종 포털 도메인, 인증서 및 방화벽 규칙은 서버 준비 후 공유·확정한다. 어느 단계든 발급된 HTTPS URL의 호스트가 분석 워커 컨테이너에서 실제로 접근 가능해야 한다.

**결과 전달 기본안(확정 필요):** DaonH&S가 결과 JSON·CSV·PNG를 자체 저장소에 보관하고 VTI 백엔드가 Bearer 인증으로 JSON과 PNG를 API에서 읽는다. VTI가 결과 저장소나 MinIO에 직접 접근할 필요는 없다. `result_image.path`는 API 기준 주소에 붙일 상대 경로다. VTI 프런트엔드에는 Bearer 키를 전달하지 않고 VTI 백엔드에서 PNG를 중계한다. CSV는 내부 저장 산출물이며 이 초안의 API 조회 대상은 아니다.

## 2. 공통 규칙

| 항목 | 현재 구현 및 제안 |
|---|---|
| 기준 주소 | VTI 임시 서버 연동 시험에 사용할 DaonH&S HTTPS 주소는 서버 준비 후 공유한다. 최종 배포에서는 포털 백엔드가 Docker 내부 서비스 주소로 분석 API를 호출한다. 아래 예시의 `https://api.daon.example`은 자리표시자다. |
| 데이터 | JSON, UTF-8. 서버 생성 시각(`created_at`, `started_at`, `completed_at` 등)은 UTC ISO 8601이다. 후보의 `captured_at`은 ATG 시각을 KST로 해석한 ISO 8601 `+09:00`이다. |
| 인증 | INT-001·003·004, 작업 취소, PNG 조회에 `Authorization: Bearer <API_KEY>` 필수. VTI 백엔드와 DaonH&S 서버만 키를 보관한다. |
| 공개 경로 | 운영 환경은 `GET /health`, `GET /version`만 공개한다. 로컬 개발 모드에서만 `/docs`, `/redoc`, `/openapi.json`을 제공한다. |
| 오류 본문 | `{"error":{"code":"...","message":"..."}}` |
| 작업 ID | 현재 `JOB-YYYYMMDD-NNNNNN`. 새로운 분석 요청마다 발급하며 같은 `Idempotency-Key` 재전송은 기존 ID를 반환한다. |
| 중복 요청 방지 | INT-003에 `Idempotency-Key`를 보내면 동일 키·동일 요청의 재전송은 기존 `job_id`를 반환한다. |
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
| `att.url`, `atg.url` | 기본안 | HTTPS GET URL. 접수 시 HTTPS·허용 호스트를 검사하며, 다운로드 시에도 재검사하고 리다이렉트를 따르지 않는다. |
| `att.object_key`, `atg.object_key` | 대안 | 서버가 접근 가능한 저장소의 상대 Object Key. URL과 동시 지정 불가. |
| `size_bytes` | 아니요 | 양의 정수. 내려받은 파일 크기 검증. |
| `sha256` | 아니요 | 64자리 16진수. 내려받은 파일의 SHA-256 검증. |
| `frame_range.start` | 아니요 | 0부터 시작하는 프레임 인덱스. 기본 0. |
| `frame_range.end_exclusive` | 아니요 | 끝 프레임을 **포함하지 않는** 인덱스. 생략 시 끝까지 분석. `start`보다 커야 한다. |
| `analysis_roi` | 아니요 | 픽셀 좌표 `{x,y,width,height}`. 좌상단 원점, `x/y ≥ 0`, `width/height ≥ 1`. 생략 시 전체 화면의 2행×3열 격자 중 하단 가운데 칸을 분석한다(384×288 입력은 `x=128,y=144,width=128,height=144`). 명시적 ROI도 그 칸 내부여야 하며, 밖이면 작업이 `FAILED`로 끝난다. |

**Q-02 운영 기본 분석 범위:** VTI 포털은 운영 요청에서 `frame_range`와 `analysis_roi`를 **항상 생략한다**. 포털 사용자에게 분석 범위 선택 기능을 제공할 필요도 없다. 이 경우 ATT의 첫 프레임부터 마지막 프레임까지 프레임을 건너뛰지 않고 처리하며, 각 프레임의 공간 범위는 원본 영상을 2행×3열로 나눈 **하단 가운데 칸(2행 2열)**이다. 검증한 384×288 입력에서는 `{x:128,y:144,width:128,height:144}`로 고정된다. 프레임 해상도가 달라지면 같은 격자 규칙으로 좌표가 계산되며, 회차별로 임의 이동하지 않는다. 이전 `x:0,y:158,width:384,height:130`은 기본 ROI 변경 전의 값이므로 운영 기본값에 적용하지 않는다. 두 선택 필드는 DaonH&S의 개발·내부 시험을 위해 API에 남겨 둔다. 아래 요청 예시는 포털의 운영 요청 형태다.

**B-07 접수 시 URL 검증:** URL 형식은 요청 모델에서 검사하고, HTTPS 여부와 DaonH&S 서버에 설정된 허용 호스트는 INT-003 접수 전에 검사한다. 위반 시 작업을 만들지 않고 HTTP 422 `SOURCE_URL_NOT_ALLOWED`를 반환한다. 이 검사는 네트워크 접속·서명 유효기간·파일 내용까지 확인하지 않는다. INT-003이 202를 반환한 뒤에도 실제 다운로드나 파일 무결성 검증에 실패하면 INT-004가 `FAILED`를 반환할 수 있다. 분석 워커는 다운로드 직전 같은 HTTPS·허용 호스트 검사를 다시 수행한다.

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

`Idempotency-Key`는 SQLite 작업 레코드에 저장되므로 API 재시작 후에도 유지된다. **같은 키·같은 요청 내용**을 다시 POST하면 새 작업 없이 기존 `job_id`·`created_at`과 **현재 상태**를 HTTP 202로 반환한다. 이미 완료되거나 실패한 작업도 같은 작업을 반환한다. 같은 키에 다른 요청 내용을 보내면 HTTP 409 `IDEMPOTENCY_KEY_CONFLICT`다. 키를 생략하면 기존 방식대로 매 POST마다 새 작업이 생성되므로 VTI는 항상 키를 보내야 중복 방지가 적용된다. 같은 `monitoring_data_id`라도 다른 키를 보내면 새 작업이다.

**C-05 응답 유실 시 접수 확인(M-01 적용):** M-01이 이미 구현되어 있으므로 VTI는 INT-003 응답을 받지 못했을 때 **최초 `Idempotency-Key`와 요청 JSON 전체를 그대로** 재전송해 기존 작업의 `job_id`를 받을 수 있다. Presigned URL을 새로 발급해 요청 본문을 바꾸면 같은 키에 대해 409가 발생한다. VTI 연동 시험에서 응답 유실·재전송과 서버 재기동 후 재전송을 검증한다. 키를 보내지 않았거나 최초 요청 본문을 보존하지 못한 예외 상황에는 이 방식으로 접수 여부를 판단할 수 없으므로 운영자 확인 절차를 사용한다. 현재 `monitoring_data_id`로 작업을 조회하는 공개 API는 없다.

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

결과 JSON의 전체 필드와 타입은 [결과 JSON Schema v0](schemas/analysis-result-v0.schema.json)에 정의되어 있다. [문서용 API 예시](examples/analysis-result-v0.example.json)와 [실제 전체 프레임 API 응답 형태 샘플](examples/int-001-real-result-20261102.json)에서 필드 구조를 확인할 수 있다. 상위 필드는 `schema_version`(현재 `0.2.0`), `job_id`, `monitoring_data_id`, `status`, `success`, `completed_at`, `processing`, `candidate_count`, `candidates`, `warnings`, `error`다. 먼저 공유한 `0.1.0` 초안에 PNG 무결성 필수 필드를 추가해 결과 형식 버전을 올렸다. API 서버 자체 버전과는 별개다. 후보에는 `candidate_id`, `rank`, 대표 프레임·시각·GPS, ROI·후보 영역, 온도 통계, 탐지 근거, `result_image`가 포함된다. GPS와 촬영시각은 없으면 `null`일 수 있다. 후보가 없으면 `candidate_count: 0`, `candidates: []`다.

**Q-03 촬영 시각·GPS:** `candidates[].captured_at`은 후보 **대표 프레임**에 대응하는 ATG 시각이다. ATG 원본에는 시간대 오프셋이 없어 현재는 장비 시각을 KST로 가정해 `2026-08-13T13:30:53+09:00` 형태로 반환한다. 이는 UTC 변환값이 아니며, 실제 장비의 시간대 설정은 DaonH&S가 확인해야 한다. 후보 대표 프레임 번호에 대응하는 ATG 레코드가 없으면 `captured_at`과 `gps`가 함께 `null`이고 결과 `warnings`에 `ATT_ATG_COUNT_MISMATCH`가 기록된다. ATG 레코드가 있으면 현재 구현은 위도·경도 숫자를 그대로 반환하며 **GPS 신호 없음만을 판별해 `gps=null`로 바꾸는 기능은 없다**. 무신호 표기 방식은 실제 ATG 샘플·장비 명세로 확인해야 한다. `captured_at`은 후보마다 다르거나 `null`일 수 있으므로 포털의 회차명 생성 기준으로 사용하지 않는다. 회차 식별자 `monitoring_data_id`는 VTI가 INT-003 요청에서 전달한다.

**Q-04 결과 경고:** 성공 결과의 `warnings`는 `[{"code":"...","message":"..."}]` 형식이며 경고가 없으면 `[]`이다. 현재 생성하는 경고는 아래 두 종류다.

| `code` | 발생 조건 | `message` 예시 |
|---|---|---|
| `ATT_ATG_COUNT_MISMATCH` | ATT 전체 프레임 수와 ATG 레코드 수가 다름 | `ATT frames (3959) and ATG records (3885) differ; unmatched candidate metadata is null.` (실제 전체 파일 결과) |
| `EMPTY_THERMAL_FRAMES_SKIPPED` | 온도 값이 비정상적이거나 전체 온도가 0 이하인 열화상 프레임을 건너뜀 | `Skipped 2 empty or invalid thermal frame(s).` (형식 예시) |

두 경고는 결과의 데이터 완전성을 알려 주며 작업을 실패로 바꾸지 않는다. 포털은 결과 상세에서 운영자가 확인할 수 있는 안내로 표시하고, 일반 실패 메시지로 처리하지 않는다. 화면 문구는 `code`로 한국어 문장을 매핑할 수 있다. ATG 불일치로 후보의 `gps`가 `null`이면 해당 후보의 지도 위치를 임의로 만들지 않는다. B-03은 후보 PNG가 하나라도 생성·저장·검증에 실패하면 작업 **전체를 `FAILED`**로 처리하므로, PNG 누락 후보를 제외하고 `warnings`에 남기는 경고 코드는 없다.

새로 분석한 후보에는 대표 프레임의 `roi_temperature_c`가 포함된다. `minimum`, `mean`, `maximum`은 **ATT의 분석 ROI 전체 픽셀 온도(°C)**의 최저·평균·최고이고, `max_minus_min`과 `max_minus_mean`은 각각 최고−최저, 최고−평균이다. 기존 `temperature_c`는 **후보 사각형**의 온도와 주변 배경 대비값이므로 구분한다. ATG는 촬영시각·GPS에 사용한다. 기존에 저장된 결과에는 `roi_temperature_c`가 없을 수 있다.

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

위 JSON 조각은 후보 객체의 **일부**다. PNG 크기는 원본 열화상 해상도를 따르며 384×288은 현재 대표 입력 예시다. PNG에는 전체 화면의 2×3 격자(흰색), 실제 분석 ROI(빨간색), 후보 영역(초록색)을 표시한다. VTI 백엔드는 `https://api.daon.example`과 `result_image.path`를 결합해 `GET` 요청을 보내고 같은 Bearer 헤더를 붙인다. 성공 응답은 `Content-Type: image/png`다. 문서용 고정 샘플 작업 `JOB-20261030-000001`에도 `MOCK` 표시가 있는 샘플 PNG를 제공한다.

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
| 422 | `SOURCE_URL_NOT_ALLOWED` | ATT·ATG URL이 HTTPS가 아니거나 허용 호스트에 없음(작업 미접수) |
| 422 | `INVALID_REQUEST` | INT-003 JSON 필드 형식·검증 오류 |
| 503 | `AUTH_NOT_CONFIGURED` | 서버에 API 키 미설정 |
| 503 | `RESULT_STORAGE_ERROR` | 완료 작업의 결과 JSON·PNG 읽기 실패 |

다운로드·파일 크기/체크섬 불일치·분석 실패 같은 **비동기 작업 오류는 POST의 HTTP 오류가 아니라 INT-004의 `status: FAILED`, `error.code/message`**로 전달된다. 현재 발생 가능한 예시는 `DOWNLOAD_FAILED`, `SOURCE_URL_NOT_ALLOWED`, `FILE_INTEGRITY_ERROR`, `INPUT_FILE_NOT_FOUND`, `ANALYSIS_FAILED`, `WORKER_INTERRUPTED`다. 자동 재시도 대상과 횟수는 [작업 상태 문서](JOB_STATE_V0.md)에 적었다.

## 7. 현재 검증과 연동 시험 전 확정 사항

현재 FastAPI·Bearer·SQLite 상태/진행률·단일 분석 워커·결과 JSON/PNG 조회가 구현되어 있다. 2026-11-02 실제 ATT·ATG 전체 3,959프레임을 로컬 Docker GPU 분석 워커에서 실행했고, 상위 50개 후보·PNG 50장·결과 JSON을 확인했다. 로컬 모의 서명 HTTPS URL 입력 작업의 배치 소요 시간은 91.362초, 저장 결과 JSON은 76,686바이트였다. `max_results=50` 상한이 적용되므로 반환된 50건을 전체 탐지 후보 수로 해석하지 않는다. 이는 **VTI MinIO·실제 네트워크 및 VTI 백엔드와의 통합 연동 검증 결과는 아니다**. 근거는 [HTTPS URL 로컬 검증](HTTPS_URL_LOCAL_TEST_2026-11-02.md)과 [CPU·GPU Docker 비교](CPU_GPU_DOCKER_COMPARISON_2026-11-02.md)다.

VTI에 전달할 시험 범위·측정값·한계의 독립적인 요약은 [T-05 실데이터 검증 기록](T05_REAL_DATA_VERIFICATION_FOR_VTI_2026-11-04.md)에 정리했다. 첫 30프레임 시험은 이전 ROI 기준이며 처리 시간 측정값이 없으므로, 포털의 시간 한도 산정에는 현재 ROI의 전체 프레임 시험 기록을 참고한다.

양측 확정이 필요한 항목은 ATT·ATG URL의 유효기간·접근 호스트, 시험 API의 HTTPS 기준 주소와 최종 배포 내부 경로, Bearer 키의 실제 전달 채널·최초 적용 및 교체 일시, 폴링 주기·최대 대기 시간, 결과의 7일 이후 삭제 시점이다. 실패 작업에만 재분석을 허용하고 이전·새 후보 ID를 연결하지 않는 정책 및 성공 결과의 최소 7일 보관에는 VTI 제안에 동의한다. M-01 중복 요청 방지는 `Idempotency-Key` 방식, M-02 분석 워커 가동 확인은 `/health` 필드, B-03 후보 PNG 제공은 성공 전 전체 재조회 검증 방식으로 구현했다. 작업별 준비·검증 상태는 [VTI 연동 시험 체크리스트](VTI_INTEGRATION_TEST_CHECKLIST.md)에 기록한다.
