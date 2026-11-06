# 분석 작업 상태·진행률 정책 v0

상태: VTI 연동 검토용 현재 구현 정책. 시험 서버 연동 결과에 따라 운영 한도는 조정할 수 있다.

## 저장 및 책임

- 분석 서비스는 SQLite `data/jobs.sqlite3`에 INT-003 요청, 상태와 결과 Object Key를 저장한다. 경로는 `HEATPIPE_JOB_DB_PATH`로 변경할 수 있다.
- 첨부 범위 자료의 PostgreSQL은 VTI 포털 백엔드 기술 스택이다. 현재 DaonH&S 분석 서비스는 API로 연동하며 VTI DB를 직접 공유하지 않는다.
- 이 구현은 단일 서버 프로세스 기준이다. 다중 서버·공유 작업 큐가 필요하면 DB와 분석 워커 구성을 다시 정한다.
- SQLite는 ATT·ATG 원본이나 결과 PNG를 저장하지 않는다. 로컬 저장소 또는 MinIO가 파일을 보관한다.

## 상태 및 진행률

`QUEUED → VALIDATING → (DOWNLOADING) → ANALYZING → RENDERING → (UPLOADING) → SUCCEEDED` 순서를 허용한다. 대기·실행 중 작업은 `CANCELLED`, 실행 중 작업은 `FAILED`로도 끝날 수 있다. 괄호 안 단계는 저장소 연동 시 사용한다. 상태가 바뀌거나 같은 실행 단계에서 진행률이 갱신될 때마다 SQLite 트랜잭션으로 기록한다.

- 새 요청은 `QUEUED`, 0%다. 별도 분석 워커를 실행하면 `VALIDATING`으로 선점된다.
- INT-004의 `queue_position`은 `QUEUED` 작업보다 먼저 접수되어 아직 대기 중인 작업의 수(0부터)다. 실행 중 작업은 세지 않으며 다른 상태에서는 `null`이다. 재시도 지연 중인 작업도 세므로 예상 시간이나 정확한 실행 순서로 해석하지 않는다.
- 배치 콜백은 `VALIDATING` 1%, `ANALYZING` 10~85%, `RENDERING` 90%, 성공 시 100%를 보고한다. 이 비율은 작업 시간의 예측치가 아니라 처리 단계의 표시값이다.
- `total_frame_count`는 요청 구간에서 분석할 프레임 수이며, `processed_frame_count`는 유효하여 분석한 프레임 수다. 빈 프레임은 전체에는 포함되지만 처리 수에서는 제외된다.
- 진행률과 처리 프레임 수는 같은 시도 안에서 감소할 수 없다. `SUCCEEDED`, `FAILED`, `CANCELLED`는 일반 진행 상태 갱신의 대상이 아니다. 재시도 가능한 `FAILED` 작업만 아래 정책에 따라 `QUEUED`로 되돌릴 수 있다.
- 단일 분석 워커 재시작 시 진행 중이던 작업은 `WORKER_INTERRUPTED` 오류로 감지하고, 남은 시도가 있으면 시작 과정에서 자동 재대기시킨다. 최대 시도 횟수를 소진했다면 `FAILED`로 남긴다. API 서버 시작은 실행 중 작업 상태를 변경하지 않는다. 대기 작업은 대기 상태로 남는다.

## 작업 취소

- `POST /api/v1/analysis-jobs/{job_id}/cancel`은 `QUEUED` 작업을 즉시 `CANCELLED`로 바꾼다. 실행 중에는 `cancel_requested=true`를 영속 저장하고 HTTP 202로 현재 상태를 반환한다. INT-004를 다시 조회해 `CANCELLED` 도달 여부를 확인한다.
- 분석 워커는 URL·로컬·MinIO 파일 다운로드 청크, 프레임 분석, 후보 렌더링, 결과 업로드 사이에 취소를 확인한다. 이미 시작한 개별 저장소 작업은 완료될 때까지 기다릴 수 있다. 작업 임시 폴더를 제거하고 업로드된 결과 객체는 삭제를 시도한다.
- 취소 요청 후 분석 워커가 재시작해도 이 작업은 `CANCELLED`로 복구하고 재시도하지 않는다. 자동 재시도 대기 중인 작업도 취소할 수 있다. `CANCELLED`에서 INT-001 결과를 제공하지 않는다.
- 이미 `SUCCEEDED` 또는 최종 `FAILED`인 작업은 취소할 수 없다(409). 포털의 시간 초과만으로는 취소되지 않으며, 실제로 분석을 포기할 때 취소 API를 호출해야 한다.

## 실패와 재시도

- 최대 시도 횟수는 3회(최초 1회와 재시도 최대 2회)다.
- INT-004의 `attempt`는 현재 시도 번호(1부터 시작), `max_attempts`는 최대 시도 횟수다. 재시도 대기 중인 `QUEUED`에서도 `attempt`는 다음 시도 번호를 표시한다.
- `INPUT_OUTPUT_ERROR`, `DOWNLOAD_FAILED`, `UPLOAD_FAILED`, `ANALYSIS_TIMEOUT`, `WORKER_INTERRUPTED`만 재시도 가능하다. 입력 오류는 재시도하지 않는다.
- INT-004의 `error.retryable`은 위 재시도 대상 **오류 유형**이면 `true`, 그 밖에는 `false`다. 이는 재실행 성공을 보장하지 않는다. 세 번째 실패 후에도 오류 유형이 같으면 `true`지만, `attempt == max_attempts`이면 해당 작업의 자동 재시도는 끝난 상태다. 자동 재시도 중인지 판단할 때는 `attempt`·`max_attempts`와 다음 상태 조회 결과를 함께 확인한다.
- `ANALYSIS_TIMEOUT`은 재시도 대상 코드로 등록되어 있으나 현재 분석 워커에 실행 시간 강제 제한은 없어, 시간 경과만으로 이 오류가 생성되지는 않는다.
- 재시도 시 같은 `job_id`를 유지하고 상태·진행률을 `QUEUED`·0%로 초기화한다. `started_at`은 재대기 중 `null`이고 다음 시도를 선점하면 새 시작 시각으로 갱신한다. `created_at`은 유지한다. 시도 횟수는 DB에 보관한다.
- 분석 워커는 재시도 가능 오류를 자동 재대기시키며 5초, 10초 지연 후 다시 선점한다. 세 번째 실패는 `FAILED`로 남긴다.
- 재시도 대상이 아닌 오류(예: `FILE_INTEGRITY_ERROR`, `SOURCE_URL_NOT_ALLOWED`, `INVALID_ANALYSIS_REQUEST`, `ANALYSIS_FAILED`, `INPUT_FILE_NOT_FOUND`)는 자동 재시도하지 않고 `FAILED`로 남긴다.
- 자동 재대기 직전이나 워커 재시작 중에는 INT-004에서 재시도 가능한 `FAILED`가 잠깐 보일 수 있다. 포털은 이런 응답을 받으면 10초 뒤 같은 `job_id`를 한 번 더 조회한다. 다음 응답이 `QUEUED` 또는 실행 중 상태면 `attempt`/`max_attempts`를 이용해 ‘2번째 시도 대기 중’처럼 안내하고, 계속 `FAILED`이면 오류를 확인해 ‘다시 실행해 주세요’ 또는 입력 수정 안내를 표시한다. `/health`의 `worker_running=false`이면 분석 워커 점검 중임을 안내한다. 현재 INT-004는 다음 재시도 시각을 제공하지 않는다.
- INT-001은 완료된 실제 작업의 결과 JSON을 반환하고 후보 PNG는 같은 API의 이미지 경로로 제공한다. 미완료 작업은 `RESULT_NOT_READY`다.
- 모든 후보 PNG를 저장한 뒤 다시 읽어 원본과 일치함을 확인해야 `SUCCEEDED`로 전환한다. 한 장이라도 실패하면 `UPLOAD_FAILED` 등으로 작업 전체를 실패 처리하고 기존 재시도 정책을 적용한다.
