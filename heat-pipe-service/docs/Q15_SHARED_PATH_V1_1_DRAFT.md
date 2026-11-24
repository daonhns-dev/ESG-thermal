# Q15 v1.1 방안 A 공유 경로 추가 명세 — 검토본

작성: 2026-11-18. **개발·Claude 검토용이며 운영 적용본/대외 확정본이 아니다.**

> 2026-11-19 내부 저장 보완(r3): 신규 및 QUEUED 갱신 request_json은 null을
> 생략한다. 새 URL/object_key 기록의 구버전 호환을 개선하며 API 외부 계약은 동일하다.
> 기존 기록과 실제 path 입력은 구버전 자동 호환 대상이 아니다. 송부본은 별도 보존한다.
기존 Q15 v1.0에 우선하는 변경점은 이 문서에 한정된다. INT-001 결과 스키마와
후보 PNG 계약, INT-004 상태 구조, 인증 및 취소 API는 유지한다.

## 1. 역할과 배포

VTI가 ATT·ATG를 `/data/data/portal/raw`에 일반 파일로 저장하고, 분석 워커는
같은 폴더를 `/shared-input`으로 읽기 전용 마운트한다. MinIO는 보고서 PDF,
분석 결과 이미지(포털 보관본), 배경지도, SHP 등 나머지 파일에 유지한다.
분석 서비스의 SQLite·결과는 `/data/data/analysis`에 별도로 보관한다.

포털 백엔드→분석 API는 `http://daon-api:8000`, Authorization Bearer 인증 유지.
공개 시험 주소 `https://heatpipe.daonhns.com`도 기존 계약대로 제공한다.
API·워커 양쪽에 `HEATPIPE_SHARED_INPUT_ROOT=/shared-input`을 설정하되,
원본 루트는 API·워커 모두 읽기 전용 마운트한다. API는 루트의 표식으로 저장소
준비 상태만 확인하고, 요청 원본의 존재·내용 검증은 워커에서 수행한다.
`portal-minio:9000`의 내부 HTTP 허용 목록은 URL 예비 방식 때문에 유지한다.

## 2. INT-003 요청

`POST /api/v1/analysis-jobs`, JSON. HTTP 202와 기존 CreateJobResponse를 반환한다.

| 필드 | 타입 | 필수/조건 | 설명 |
|---|---|---|---|
| monitoring_data_id | string | 기존 필수 | 기존 식별자·문자 제한 유지 |
| att.path / atg.path | string | 경로 방식 선택 시 필수 | /shared-input 기준 POSIX 상대 파일 경로 |
| att.size_bytes / atg.size_bytes | positive integer | path 방식에서 필수 | 원본의 실제 바이트 수 |
| att.sha256 / atg.sha256 | string(64 hex) | path 방식에서 필수 | 원본 SHA-256, 대소문자 무관 |
| att.url / atg.url | HTTP(S) URL | 예비 다운로드 방식 | 기존 URL 허용 목록·만료·재다운로드 규칙 유지 |
| att.object_key / atg.object_key | string | 기존 저장소 방식 유지 | 기존 저장소 어댑터로 fetch, 공유 path로 해석하지 않음 |
| frame_range / analysis_roi | object | 기존 선택 | 기존 처리 유지 |

파일별 path, url, object_key 중 **정확히 하나만 non-null**이어야 한다.
파일별로 경로/URL 혼합도 허용한다. path 방식의 size_bytes·sha256 필수화는 새
방식에만 적용되며 기존 URL/object_key 요청의 선택 조건은 그대로다.

`2026/11/upload-id.att` → 워커 `/shared-input/2026/11/upload-id.att`.
절대 경로, `..`, `.`, 빈 경로 요소, 백슬래시, 드라이브/URL 표기, NUL은 거부한다.
path는 URL이 아니므로 percent decoding하지 않는다. 경로를 URL 인코딩하지 않는다.
심볼릭 링크를 해석한 실제 파일이 공유 루트 밖에 있어도 워커가 거부한다.
API는 구문/서버 설정/루트 표식만 검증하고, 파일 존재·크기·해시는 비동기 작업에서 검증한다.
요청에 호스트 경로나 /shared-input 접두사를 넣지 않는다. ATT·ATG 원본명은 서로
달라도 된다. 결과 metadata는 기존처럼 monitoring_data_id.att/.atg를 사용한다.

요청 예시: `examples/int-003-shared-path.example.json`.
예시의 크기·해시는 합성 시험 파일 기준이며 실제 업로드 파일 값으로 교체한다.
OpenAPI 정적 사본의 FileReference에도 path 및 조건을 반영했다.

## 3. 중복 요청과 재전송

Idempotency-Key 비교값은 기존대로 monitoring_data_id와 ATT/ATG 각각의
size_bytes, sha256이다. 위치(path/url/object_key)는 비교하지 않는다.
같은 키·내용이면 기존 job_id를 반환한다. QUEUED인 경우 파일 참조만 새 참조로
갱신하며 frame_range/ROI 등은 기존 값 유지. 실행/완료/실패/취소 상태에서는
파일 참조를 바꾸지 않는다. 내용 비교값이 다르면 409 IDEMPOTENCY_KEY_CONFLICT.
키는 기존대로 작업 기록이 있는 동안 보관하며 자동 만료는 없다.

## 4. 상태·실패·취소·보관

path 입력도 상태값 호환성을 위해 DOWNLOADING 단계를 유지하지만 상태 문구는
"Preparing ATT/ATG files"다. 네트워크 다운로드 발생을 의미하지 않는다.
검증 원본을 작업 폴더에 심볼릭 링크로 연결해 배치의 기존 파일명 요구를 맞추며
원본 바이트는 복사하지 않는다. 누락/검증 실패 시 URL로 자동 전환하지 않는다.
필요하면 포털이 유효 URL로 새 요청을 발급한다(실패 작업에 같은 키를 쓰면 기존
작업만 반환되므로 새 분석은 새 키 사용).

INPUT_FILE_NOT_FOUND·FILE_INTEGRITY_ERROR·SHARED_INPUT_NOT_CONFIGURED 등으로
최종 실패한 뒤 파일이나 설정을 고쳐도 같은 키로는 기존 FAILED 작업이 반환된다.
수정 후 재분석은 새 Idempotency-Key로 요청한다. POST가 503으로 거부된 경우에는
작업/키 기록 자체를 만들지 않으므로 같은 키로 재접수할 수 있다.

성공/실패/취소 후 임시 폴더 정리는 링크와 임시 결과만 제거하고 원본은 보존한다.
포털이 원본 쓰기·이동·삭제를 담당하며 대기·실행·재시도 중에는 변경하지 않는다.
기존 등록 원본 보관 기간은 별도 미확정이며 이번 변경으로 자동 삭제를 추가하지 않는다.
기존 취소·3회 시도·워커 복구 정책과 결과 저장 정책은 유지한다.

## 5. 오류 추가 및 유지

HTTP 오류는 모두 `{"error":{"code":"…","message":"…"}}` JSON이다.

| 전달 위치 | code | 의미/재시도 |
|---|---|---|
| POST 422 | INVALID_REQUEST | path 구문·위치 중복·크기/해시 누락 등 |
| POST 503 | SHARED_INPUT_NOT_CONFIGURED | API의 공유 루트 미설정/상대 경로 설정/표식 누락·불일치·읽기 실패 |
| INT-004 error | SHARED_INPUT_NOT_CONFIGURED | 워커 공유 루트 미설정/작업 폴더와 겹침/표식 누락·불일치·읽기 실패. 자동 재시도 없음 |
| INT-004 error | INVALID_SHARED_PATH | 공유 루트 밖으로 향하는 경로 등. 자동 재시도 없음 |
| INT-004 error | INPUT_FILE_NOT_FOUND | 일반 원본 파일 없음 또는 디렉터리. 자동 재시도 없음 |
| INT-004 error | FILE_INTEGRITY_ERROR | size/SHA-256 불일치. 자동 재시도 없음 |
| INT-004 error | INPUT_OUTPUT_ERROR | 읽기 권한·입출력 문제. 기존 자동 재시도 유형 |

URL 방식의 SOURCE_URL_NOT_ALLOWED/DOWNLOAD_FAILED 및 기존 HTTP·작업 오류는
기존 Q15와 오류 문서를 따른다. 공유 폴더는 신뢰된 포털이 관리하는 불변 원본을
전제로 한다. 포털이 검증/분석 중 원본을 교체하는 경쟁 상황까지 방어하는 설계는 아니다.
운영 경로가 작업 오류에 포함될 수 있으므로 포털 화면에는 그대로 노출하지 않는다.

## 6. 배포와 검증 조건

서버 env에 아래 값을 설정하고, 기존 Compose 세 파일 뒤에
`compose.external-data.yaml`, `compose.shared-input.yaml`을 추가한다.

```dotenv
HEATPIPE_SHARED_INPUT_HOST_ROOT=/data/data/portal/raw
HEATPIPE_EXTERNAL_ANALYSIS_ROOT=/data/data/analysis
```

두 대상 폴더는 미리 있어야 하며 자동 생성하지 않는다. 기존 분석 /data의 내용을
이전하지 않고 새 폴더로 재생성하면 기존 작업이 사라져 보이므로 정지·백업·복사·
검증이 선행돼야 한다. 중지된 Caddy https 서비스는 시작하지 않는다.

공유 원본 폴더에는 운영자가 실제 `/data` 디스크 마운트를 확인한 뒤
`.heatpipe-shared-root` 일반 파일을 준비한다. 정확한 내용은
`heatpipe-shared-input-v1` 뒤 LF 한 개다. 표식은 API·워커가 읽기만 하며 자동 생성하지 않는다.
표식 symlink, 빈 파일, 잘못된 내용, 읽기 불가 상태는 구성 오류다.
기본 compose.yaml은 HEATPIPE_SHARED_INPUT_ROOT를 주입하지 않으며, 오버레이에서만
두 서비스의 루트를 활성화한다. URL 입력은 표식이 없어도 기존 방식으로 동작한다.

표식은 빈 마운트 지점 오인 방지 장치이지 디스크 UUID 검증은 아니다. 루트 디스크에
잘못 생성하면 보호가 무력화되므로 findmnt로 외부 디스크를 확인한 뒤에만 준비한다.
예정된 실제 경로에 디스크가 연결된 상태에서 아래 내용을 읽을 수 있어야 한다.

```text
/shared-input/.heatpipe-shared-root -> heatpipe-shared-input-v1\n
```

워커가 비정상 종료돼 작업 폴더가 남아 있어도 다음 시도는 각 입력 링크를 다시
연결한다. 연결 전 destination.unlink는 작업 폴더 내 항목만 제거하며 원본은 보존한다.
API·워커의 포트·네트워크·restart·로그 설정을 유지한다. 실제 root 디스크 mount와
공유 파일 가용성, 권한, 서비스 기동 순서도 점검한다.
Docker의 /data 마운트 기동 의존성, 잘못 생성된 빈 분석 DB 점검 및 스토리지 장애 후
새 키 재요청 절차는 [전환 체크리스트](EXTERNAL_STORAGE_MIGRATION_2026-11-18.md)를 따른다.

기존 원본은 MinIO API로 내보내 크기/SHA-256 대조 후 VTI가 DB 파일 위치를 갱신한다.
내부 객체 폴더를 단순 복사하지 않는다. 전환이 끝날 때까지 기존 데이터는 보존한다.
구버전 워커는 path 요청을 해석하지 못하므로 롤백은 신규 접수 중단과 대기 작업·
DB·파일 참조 정합성 확인 후 진행한다.

제안 일정: 11/19 필드·경로 공유, 11/23 오후 수정본·명세 전달 목표,
11/24 이전·통합 시험. 중단 시간대와 VTI 착수/완료 가능 여부는 양사 확인 필요.
이 문서는 일정 확약이나 서버 적용 완료를 뜻하지 않는다.

통합 시험: 기존 회차 조회·재분석, 신규 업로드·분석·JSON·PNG, 원본 크기·해시,
URL 예비 방식, 혼합 입력, 재전송, 취소·실패·재시도 후 원본 보존,
MinIO의 나머지 파일 조회, 스토리지 전환 후 재기동. 실제 포털 검증은 미실시다.
