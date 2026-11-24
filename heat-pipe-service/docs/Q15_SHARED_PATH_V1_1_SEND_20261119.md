# Q15 연동 API 변경 명세 v1.1

ATT·ATG 공유 원본 직접 읽기 - 방안 A

- 제공일: 2026-11-19 / 제공: DaonH&S / 수신: VTI
- 용도: VTI 포털 개발 착수용 송부본. DaonH&S 구현·로컬 검증 기준이다.
- 적용 상태: **시험 서버에는 아직 미적용.** 양사 배포·데이터 이전·통합 시험 후 전환한다.
- 범위: 기존 Q15 v1.0에 공유 경로 입력을 추가한다. 변경 사항은 본 명세가 우선한다.

## 1. 변경 범위와 접속

ATT·ATG만 일반 파일 공유로 전환한다. 보고서 PDF·포털 보관 결과 이미지·배경지도·SHP 등은 MinIO를 유지한다. 사용자 업로드, 분석 요청, 상태·결과 조회 흐름은 유지한다.

| 항목 | v1.1 동작 |
|---|---|
| INT-003 | att.path·atg.path 추가. 경로 입력은 다운로드 없이 읽음 |
| URL 입력 | 예비 방식 유지. URL은 기존처럼 다운로드 |
| INT-004·작업 취소 | 기존 API·상태값·시도 횟수 유지 |
| INT-001·후보 PNG | 기존 응답·결과 Schema·크기 및 SHA-256 유지 |
| 인증 | 기존 Bearer 인증. 키는 별도 전달하며 본 자료에 포함하지 않음 |

포털 백엔드의 내부 API 주소는 `http://daon-api:8000`, 기존 공개 API 주소는 `https://heatpipe.daonhns.com`이다. 공개 주소가 현재 응답하더라도 새 path 계약이 적용됐다는 뜻은 아니다. 전환 완료는 별도 안내한다.

```text
포털 업로드 → 공유 원본 폴더에 일반 ATT·ATG 게시
→ INT-003(path, size_bytes, sha256) → 워커 직접 읽기
→ INT-004 상태 조회 → SUCCEEDED → INT-001 결과·PNG 조회
```

## 2. 경로와 저장소 역할

아래 표는 **제안 배치이며 양사 확인 후 확정**한다.

| 서버 호스트 경로 | 용도/컨테이너 경로 |
|---|---|
| /data/data/portal/raw | ATT·ATG 원본. 분석 API·워커는 /shared-input:ro |
| /data/data/analysis | 분석 API·워커 /data: 작업 DB·결과·임시 작업 |
| /data/data/portal/db | 포털 DB. VTI의 기존 컨테이너 내부 경로 유지 |
| /data/data/portal/minio | 나머지 파일. MinIO /data |

`att.path`가 `2026/11/example-att.att`이면 워커는 `/shared-input/2026/11/example-att.att`를 읽는다. 요청에 서버의 `/data/data/...`, `/shared-input`, MinIO bucket 이름을 덧붙이지 않는다. 실제 하위 배치는 위 상대 경로 규칙에 맞춰 양사가 정한다.

<!-- pagebreak -->

## 3. INT-003 요청 필드

`POST /api/v1/analysis-jobs` / `Content-Type: application/json`

`Authorization: Bearer <별도 전달 키>` / `Idempotency-Key: <요청 식별 키>`(선택)

| 필드 | 타입 | 필수 조건 | 설명 |
|---|---|---|---|
| monitoring_data_id | string | 필수 | 1~128자, 영문·숫자·하이픈·밑줄 |
| att, atg | object | 각각 필수 | 아래 파일 참조 구조 |
| att.path, atg.path | string | path 방식 | 공유 루트 기준 POSIX 상대 경로 |
| att.size_bytes, atg.size_bytes | integer | path에서 필수 | 실제 바이트 수. 0보다 큼 |
| att.sha256, atg.sha256 | string | path에서 필수 | 64자리 16진수. 대소문자 무관 |
| att.url, atg.url | HTTP(S) URL | URL 방식 | 기존 Presigned GET URL |
| att.object_key, atg.object_key | string | 기존 방식 | 기존 저장소 어댑터 입력. 공유 path가 아님 |
| frame_range | object | 선택 | start=0 기본. end_exclusive 생략 시 끝까지 |
| analysis_roi | object | 선택 | 기존 v1.0의 x·y·width·height 규칙 유지 |

각 파일에서 **path·url·object_key 중 정확히 하나만 non-null**로 보낸다. path와 url을 한 파일 객체에 함께 보내면 422다. path에서 size_bytes 또는 sha256이 없으면 422다. 기존 URL/object_key 방식은 크기·해시 선택 조건을 유지한다. ATT는 path, ATG는 url처럼 파일별 혼합 입력도 허용한다.

frame_range.start는 0 이상, end_exclusive는 start보다 커야 한다. analysis_roi를 생략하면 기존 분석 영역을 사용하며, 처리 가능한 실제 범위는 기존 v1.0·OpenAPI를 따른다.

## 4. 경로 검증과 파일 무결성

- 절대 경로, `.`·`..` 요소, 빈 경로 요소, 백슬래시, 콜론, NUL을 거부한다.
- path는 URL이 아니다. URL 인코딩/디코딩을 적용하지 않는다.
- 심볼릭 링크를 해석한 실제 경로가 공유 루트 밖이면 거부한다.
- 개별 원본이 없거나 디렉터리이면 INPUT_FILE_NOT_FOUND다.
- 워커는 분석 전에 원본 크기와 SHA-256을 모두 대조한다.
- API는 구문·루트 준비 상태를 확인한다. 원본 존재·무결성은 워커가 비동기로 확인한다.
- ATT·ATG의 원본 파일명과 stem은 달라도 된다. 결과의 입력 파일명은 기존처럼 monitoring_data_id.att/.atg를 사용한다.

포털은 임시 파일에 저장하고 검증을 마친 뒤 rename하여 원본을 공개하고 분석 요청해야 한다. 대기·분석·재시도 중 원본을 이동·교체·삭제하지 않는다. 워커는 원본을 읽기만 하며 작업 종료 후 원본을 삭제하지 않는다.

<!-- pagebreak -->

## 5. 공유 경로 요청 예시

다음 JSON은 첨부한 소형 합성 ATT·ATG와 실제로 일치한다. 실제 업로드에는 해당 파일의 크기·해시를 계산해 사용한다. 합성 파일은 경로 연동 시험용이며 실제 촬영 품질·성능 검증 자료가 아니다.

```json
{
  "monitoring_data_id": "shared-path-example",
  "att": {
    "path": "2026/11/example-att.att",
    "size_bytes": 49160,
    "sha256": "746bf3721b174f836b8ab6bf8dbf6eee4063ad160563bbe110e6efb14bf24c1a"
  },
  "atg": {
    "path": "2026/11/example-atg.atg",
    "size_bytes": 156,
    "sha256": "1e50d7bce6c680567af29a1d912651fa7d938ac27db25796ff6fc0c56820112a"
  },
  "frame_range": {"start": 0, "end_exclusive": 4}
}
```

HTTP 202 응답은 접수 확인이며 분석 완료가 아니다. 응답 형태는 유지한다. 아래 job_id와 시각은 설명용 가상 값이다.

```json
{
  "job_id": "JOB-20261119-000001",
  "monitoring_data_id": "shared-path-example",
  "status": "QUEUED",
  "created_at": "2026-11-19T00:00:00Z"
}
```

## 6. URL 예비 방식

기존 `att.url`, `atg.url`은 유지한다. 설정한 허용 HTTPS 호스트와 명시적으로 허용한 내부 HTTP 호스트만 사용한다. `portal-minio:9000`의 내부 HTTP 허용 설정은 유지한다. URL 입력은 원본 공유 폴더가 미준비인 경우에도 기존 다운로드 방식으로 처리한다.

URL의 24시간 유효기간·서명은 VTI가 발급하며 실제 유효 여부는 다운로드 시 확인한다. path 입력은 URL 만료의 영향을 받지 않는다. path가 누락되거나 검증에 실패해도 URL로 자동 전환하지 않는다. 포털이 예비 URL 방식을 명시해 별도 요청한다.

URL 및 혼합 요청은 `examples/`의 추가 JSON을 참고한다. 예시 URL의 서명은 가상 값이며 실제 접속할 수 있는 URL이 아니다.

<!-- pagebreak -->

## 7. 상태·결과·재전송

작업 흐름은 QUEUED → VALIDATING → DOWNLOADING → ANALYZING → RENDERING → UPLOADING → SUCCEEDED를 유지한다. 실패·취소·자동 재시도는 기존 정책을 따른다.

path 입력도 포털 호환성을 위해 DOWNLOADING 상태를 사용한다. 상태 문구는 `Preparing ATT/ATG files`이며 이때 원본을 다운로드하거나 복사하지 않는다. 완료 후 INT-004의 SUCCEEDED·100%와 INT-001 결과 JSON·PNG를 조회한다. 결과 Schema와 후보 PNG의 크기·SHA-256 계약은 변경하지 않는다.

Idempotency-Key 비교값은 monitoring_data_id와 ATT·ATG 각각의 size_bytes·sha256이다. path/url/object_key 위치는 비교하지 않는다. 같은 키와 같은 내용이면 기존 job_id를 반환한다. QUEUED일 때만 파일 참조를 갱신하고, 실행·완료·실패·취소 작업의 참조는 변경하지 않는다. 비교값이 다르면 409 IDEMPOTENCY_KEY_CONFLICT다. 키는 작업 기록이 남아 있는 동안 보관하며 자동 만료는 없다.

URL·object_key에서 size_bytes·sha256을 생략하면 생략한 값으로 파일 내용을 비교하지 않는다. 단, 생략은 null 비교값으로 취급하므로 재전송에서 해당 값을 새로 제공하거나 생략하면 기존 비교값과 달라져 409가 된다. 두 요청 모두 생략했다면 그 항목으로 내용 동일성을 확인할 수 없다.

**최종 FAILED 작업은 파일·설정을 고쳐도 같은 키로 재실행되지 않는다.** 원인을 해결한 뒤 새 Idempotency-Key로 새 분석을 요청한다. POST가 503으로 거부돼 접수되지 않았다면 작업·키 기록이 없으므로 같은 키를 다시 사용할 수 있다.

## 8. 추가·변경 오류 조건

HTTP 오류는 `{"error":{"code":"...","message":"..."}}` JSON이다. 작업 오류는 기존 INT-004 HTTP 200 응답의 error에 code·message·retryable로 제공한다.

| 발생 위치 | error.code | 의미 |
|---|---|---|
| POST 422 | INVALID_REQUEST | 경로 구문, 위치 중복 또는 path 무결성 정보 누락 |
| POST 503 | SHARED_INPUT_NOT_CONFIGURED | 루트 미설정·표식 누락/불일치·준비 상태 확인 실패 |
| INT-004 | SHARED_INPUT_NOT_CONFIGURED | 워커 루트/표식 문제 또는 작업 경로와 겹침 |
| INT-004 | INVALID_SHARED_PATH | 실제 경로가 공유 루트 밖을 가리킴 |
| INT-004 | INPUT_FILE_NOT_FOUND | 원본 파일 없음 또는 일반 파일이 아님 |
| INT-004 | FILE_INTEGRITY_ERROR | size_bytes 또는 SHA-256 불일치 |
| INT-004 | INPUT_OUTPUT_ERROR | 읽기 권한·디스크 입출력 문제 |

위 작업 오류 중 INPUT_OUTPUT_ERROR만 기존 자동 재시도 유형이다. 나머지는 자동 재시도하지 않는다. 기존 DOWNLOAD_FAILED·UPLOAD_FAILED·WORKER_INTERRUPTED 등의 재시도 정책은 유지한다. 추가된 경로 오류 때문에 서버 내부 경로가 포털 이용자 화면에 노출되지 않도록 한다. 전체 HTTP·작업 오류 코드는 동봉한 ERROR_CODES.md를 따른다.

<!-- pagebreak -->

## 9. 공유 루트 준비와 운영 경계

원본 폴더에는 일반 파일 `.heatpipe-shared-root`를 준비한다. 내용은 `heatpipe-shared-input-v1` 뒤 LF 한 개다. 표식은 symlink이면 안 된다. 운영자가 외부 디스크를 확인한 뒤 생성하며 서비스가 자동 생성하지 않는다.

API는 path 요청마다 표식을 확인하고, 미준비이면 503으로 거부한다. 워커도 직접 읽기 전에 확인한다. 접수 이후 스토리지가 미준비 상태가 되면 해당 작업은 SHARED_INPUT_NOT_CONFIGURED로 실패할 수 있다. 복구 후 새 키로 재요청한다.

표식은 올바른 외부 디스크 연결을 증명하는 UUID 검증 장치가 아니다. Docker가 외부 디스크보다 먼저 시작되지 않도록 호스트 마운트 기동 순서를 설정하고, 디스크 미연결 상태에서 루트 디스크의 같은 경로에 빈 jobs.sqlite3가 생기지 않도록 확인해야 한다. 적용 시간대와 호스트 설정은 양사·서버 담당자가 맞춘다.

API·워커 원본 마운트는 읽기 전용이다. 포털만 원본 저장·삭제를 수행하며, 원본은 대기·분석·재시도 동안 불변이어야 한다. 현재 코드의 경로·해시 검증은 이 운영 조건을 전제로 한다.

## 10. 기존 데이터 이전과 통합 시험

- 기존 MinIO의 ATT·ATG는 S3 API로 내보내 크기·SHA-256·건수를 확인하고 포털 DB의 파일 위치를 갱신한다. MinIO 객체 디렉터리를 일반 원본처럼 복사하지 않는다.
- DB·MinIO·분석 데이터는 쓰기를 중지하고 백업·이전·검증·재기동한다. 확인 완료 전까지 기존 데이터와 볼륨은 보존한다.
- 보고서·결과 이미지 등 나머지 파일은 MinIO에 유지한다. 기존 사용자 조회·다운로드·재분석 흐름을 유지한다.
- 원본 보관 기간과 자동 삭제는 이번 위치 변경과 별개다. 새 보관·삭제 정책을 임의로 추가하지 않는다.
- 롤백 시 신규 접수를 중단하고 **대기열을 비운 뒤에만** 구버전으로 전환한다. 대상은 v1.1에서 접수된 모든 대기 작업이며 URL 입력도 포함한다. URL 요청도 저장 JSON에 `path: null`이 포함되므로 구버전이 읽지 못한다. 실행 중 작업과 자동 재시도 대기까지 종료·정리하고, 전환 후 DB·파일의 정합성과 복구 범위를 함께 확인한다.

통합 시험에서는 신규 업로드·분석·상태·JSON·PNG, 기존 회차 조회·재분석, URL 예비 방식, 경로 오류·무결성 오류, 재전송·취소·실패·재시도 후 원본 보존, 서버 재기동 후 마운트와 기존 DB 유지를 확인한다.

## 11. 송부 시점 상태와 일정

2026-11-19 기준: DaonH&S 로컬 구현·코드 검토 완료, Linux 단위 테스트 77개 통과. 실제 VTI 포털 수정과 서버 전환·데이터 이전·통합 시험은 아직 완료하지 않았다. 본 자료는 개발 착수를 위한 명세 제공이며 신규 계약의 서버 제공 완료 통보가 아니다.

양사 통합 시험 완료 목표는 11월 24일이다. 구체적인 서버 적용·포털 중단 시간대는 VTI 반영 범위와 준비 상태를 확인해 별도 협의한다.

동봉 OpenAPI는 전체 INT-001·003·004 계약이다. info.version은 서비스 구현 버전, info.x-contract-version은 본 명세 버전 v1.1을 나타낸다. 요청 예시·합성 파일의 크기·해시는 송부 묶음에서 교차 검증했다.
