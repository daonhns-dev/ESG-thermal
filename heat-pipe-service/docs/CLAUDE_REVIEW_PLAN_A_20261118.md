# Claude 검토 요청: 방안 A 공유 원본 직접 읽기

> 최신 r3 추가 검토는 CLAUDE_REVIEW_ROLLBACK_STORAGE_R3_20261119.md를 먼저 읽는다.
> r3는 요청 저장 두 곳의 exclude_none 보완이며 87개 단위 테스트를 통과했다.
> 아래 r2 기록(77개)은 이전 검토 결과로 보존한다. 서버 미적용 상태는 동일하다.

2026-11-18 로컬 검토본. 서버 전송·적용·컨테이너 재생성·기존 데이터 이전·커밋·푸시는 하지 않았다.

Claude r2 재검토: 추가 결함 없음(코드·설정·문서 읽기 검토, 테스트 미실행).
후속 운영 지적 2건은 EXTERNAL_STORAGE_MIGRATION_2026-11-18.md에 반영했다.
Docker의 /data 마운트 기동 의존성과 잘못 생성된 분석 DB 확인, 스토리지 복구 후
SHARED_INPUT_NOT_CONFIGURED 실패 작업을 새 키로 재요청하는 절차다.
이 후속 반영은 문서 변경뿐이며 77개 통과는 직전 r2 코드의 Linux 실행 결과다.

## r2: Claude 1차 검토 반영

- 중단 복구 시 남은 링크: symlink 생성 직전에 작업 폴더의 destination을 unlink한다.
  원본 파일이 아니라 링크/로컬 입력 항목만 제거한다. 1·2번째 시도 중 중단 후 실제
  recover_interrupted/requeue_pending_retries 호출을 거쳐 성공하는 회귀 테스트 추가.
  두 링크/ATT만 남음/끊어진 링크를 모두 시험했다.
- 스토리지 연결 확인: 공유 루트에 일반 파일 `.heatpipe-shared-root`를 요구한다.
  내용은 `heatpipe-shared-input-v1` + LF. 운영자가 외부 디스크 확인 후 생성하고
  코드가 자동 생성하지 않는다. API·워커 모두 원본 루트를 ro 마운트한다.
  API는 매 path 요청마다 표식을 확인하고 미준비 시 503(작업 생성 없음), 워커도
  처리 시 확인해 구성 오류를 입력 누락과 구분한다. 표식 symlink는 거부한다.
- 기본 Compose에서 공유 루트 변수 주입 제거. 명시적 오버레이에서만 활성화한다.
- 최종 실패 후 수정·재분석은 새 Idempotency-Key가 필요함을 명세에 추가했다.
- Linux 단위 테스트 **77 passed**. URL 방식은 표식 누락에도 동작함을 검증했다.
- 표식은 디스크 UUID 검증이나 host mount 기동 의존성의 대체가 아니다.
  외부 디스크에만 생성하고 systemd/fstab 기동 순서는 실제 서버에서 별도 확인한다.

## Claude에게 전달할 요청

VTI 포털과 DaonH&S 분석 서비스가 같은 Ubuntu 시험 서버에 배포돼 있습니다.
방안 A는 ATT·ATG만 일반 파일 공유로 전환해 워커가 다운로드 없이 읽고,
보고서·결과 이미지·배경지도·SHP 등은 MinIO에 유지하는 방식입니다.
아래 변경을 검토하고 오류나 누락을 심각도와 파일/라인 근거로 알려 주세요.
우선 코드 검토만 하고 서버 배포나 기존 데이터 변경은 하지 말아 주세요.

특히 다음을 검토해 주세요.

1. FileReference의 path/url/object_key 배타 선택과 기존 저장된 request_json 호환성.
2. path 요청에서 크기·SHA-256 필수, 상대 경로 구문·루트 밖 symlink·파일 유형 검증.
3. path만 직접 읽고 URL/object_key는 기존 fetch를 유지하는지, 혼합 요청도 가능한지.
4. 성공/실패/취소/재시도/워커 중단 복구 후 공유 원본 보존과 임시 링크 정리.
5. 대기 중 동일 키 재전송 시 참조 갱신이 안전한지. identity는 기존대로
   monitoring_data_id와 두 파일의 size_bytes/sha256이며 경로는 비교에서 제외.
6. 상태·결과·PNG·오류 계약과 OpenAPI·명세·테스트의 일치 여부.
7. Compose 병합에서 기존 /data가 bind로 정확히 대체되고 api alias·포트·로그·
   restart가 유지되는지. API·워커 원본 마운트가 모두 ro인지.
8. VTI 포털 준비·기존 DB/파일 이전 전 적용되지 않도록 절차가 충분한지.

## 구현 변경

| 파일 | 변경 |
|---|---|
| src/heatpipe_service/api/models.py | FileReference.path, 상대 경로 검증, 정확히 하나의 위치 선택, path 무결성 정보 필수 |
| src/heatpipe_service/api/app.py | path 요청의 공유 루트 설정 확인, 미설정 시 503 |
| src/heatpipe_service/storage/shared_inputs.py | root 내 일반 파일 확인·size/hash 검증·임시 symlink. HTTP나 입력 복사 없음 |
| src/heatpipe_service/jobs/worker.py | 입력별 path/fetch 분기. 기존 batch 명명 규칙·정리·결과 흐름 유지 |
| src/heatpipe_service/storage/backends.py | path가 fetch 어댑터로 잘못 들어오면 명시적 거부 |
| src/heatpipe_service/jobs/store.py | 동일 내용의 QUEUED 재전송에서 파일 참조 갱신; 실행 이후 변경 안 함 |
| compose.yaml / .env.example | 기본 Compose에서 공유 루트 주입 제거, 오버레이에서만 활성화 |
| compose.external-data.yaml | 분석 API·워커의 /data를 이전 완료된 외부 분석 폴더로 연결 |
| compose.shared-input.yaml | API·워커 모두 원본 ro 마운트와 루트 설정, API도 표식 확인 |
| tests/unit/test_shared_inputs.py | path 검증·입력 혼용 금지·idempotency·설정 누락·symlink 탈출·hash 오류 |
| tests/unit/test_storage_worker.py | 직접 읽기·원본 보존·재시도·URL 예비/혼합 방식·PNG 검증 |
| docs/openapi.json | 현재 코드에서 재생성 |
| docs/Q15_SHARED_PATH_V1_1_DRAFT.md | 새 계약·경로·오류·배포·일정 검토본 |
| docs/examples/int-003-shared-path.example.json | 실제 합성 ATT·ATG와 일치하는 요청 예시 |
| scripts/build_shared_path_example.py | 합성 원본과 요청 예시 재생성 |

Q15 v1.0 문서에는 v1.1 검토본 안내만 추가했다. 결과 JSON Schema는 변경하지 않았다.
기존 최초 직접 읽기 초안의 URL→파일 경로 매핑은 폐기했다. URL 방식은 항상 fetch한다.

## 제안 인터페이스

- 서버 `/data/data/portal/raw` → 워커 `/shared-input:ro`.
- path는 `2026/11/id.att`처럼 원본 루트 기준 상대 경로. bucket/호스트/컨테이너 루트
  접두사 없이 사용. size_bytes와 sha256 필수.
- 분석 데이터는 `/data/data/analysis` → API·워커 `/data`.
- 원본 path가 없을 때 URL로 자동 대체하지 않는다. 예비 URL은 요청에서 명시한다.
- API·워커는 shared root 표식을 확인하고, 개별 원본 파일 존재/무결성은 워커가 확인한다.
- VTI가 임시 업로드→hash 검증→rename 공개를 수행하고 실행/재시도 중 파일을
  이동·교체·삭제하지 않는 조건이다. ro 마운트만으로 다른 컨테이너의 쓰기를 막지는 못한다.

## 검증 결과

- Linux Python 3.12 임시 가상환경에서 `python -m pytest tests/unit -q`: **77 passed** (r2).
- OpenAPI 생성 결과와 정적 사본 일치 테스트 통과. 기존 422 설명 문구 차이는
  이번 코드 기준 사본 재생성으로 해소됐다.
- Compose 5.3.1 병합 확인: api/worker /data는 외부 analysis bind 1개씩,
  api/worker /shared-input은 원본 bind 1개씩(ro). daon-api 별칭·default/shared 네트워크·
  18001 포트·unless-stopped·10m/5 로그 정책 유지 확인.
- Windows 기존 가상환경은 패키지 충돌과 symlink 권한 제약이 있어 검증 환경으로
  사용하지 않았다. 서버와 같은 Linux에서 검증했다.
- PyTorch가 없는 임시 환경이므로 golden 알고리즘 대조 테스트는 미실행.
  이번 작업에서 알고리즘 코드는 수정하지 않았다.
- 실제 VTI 포털 업로드/기존 데이터 이전/외부 스토리지 전환/재부팅은 미검증.

## 배포 전 남은 사항

- VTI의 경로 레이아웃·metadata 필수 조건·URL 예비 방식에 대한 최종 합의.
- 포털 업로드·원본 조회·재분석·보관/삭제의 일반 파일 어댑터 구현.
- 포털 DB/MinIO와 분석 /data의 백업·정지·복사·hash 검증·복구 절차 및 시간대.
- 공유 스토리지 불변 원본 조건과 host uid/gid, 마운트 기동 의존성 확인.
- 기존 회차 재분석 및 신규 원본의 다운로드 없는 전체 통합 시험.

## 검토 범위 주의

이 작업 전부터 미커밋 변경이 있었다. 특히 analysis/algorithms.py, experiments,
일부 문서·URL 허용 호스트 수정은 이전 작업에서 남아 있던 내용이다. 임의로 되돌리거나
이번 공유 경로 구현의 일부로 단정하지 않는다. 검토 ZIP은 관련 파일 스냅샷이며
서버 배포 패키지가 아니다. 루트 `.env`, 비밀키, 원본 실데이터는 포함하지 않는다.
