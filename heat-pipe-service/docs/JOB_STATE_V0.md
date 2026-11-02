# 분석 작업 상태·진행률 정책 v0

상태: DaonH&S 내부 구현안. VTI 연동 계약의 확정본은 아니다.

## 저장 및 책임

- 분석 서비스는 SQLite `data/jobs.sqlite3`에 INT-003 요청, 상태와 결과 Object Key를 저장한다. 경로는 `HEATPIPE_JOB_DB_PATH`로 변경할 수 있다.
- 첨부 범위 자료의 PostgreSQL은 VTI 포털 백엔드 기술 스택이다. 현재 DaonH&S 분석 서비스는 API로 연동하며 VTI DB를 직접 공유하지 않는다.
- 이 구현은 단일 서버 프로세스 기준이다. 다중 서버·공유 작업 큐가 필요하면 DB와 실행기 구성을 다시 정한다.
- SQLite는 ATT·ATG 원본이나 결과 PNG를 저장하지 않는다. 로컬 저장소 또는 MinIO가 파일을 보관한다.

## 상태 및 진행률

`QUEUED → VALIDATING → (DOWNLOADING) → ANALYZING → RENDERING → (UPLOADING) → SUCCEEDED` 순서를 허용한다. 실행 중 상태는 `FAILED`로 끝날 수 있다. 괄호 안 단계는 저장소 연동 시 사용한다. 상태가 바뀌거나 같은 실행 단계에서 진행률이 갱신될 때마다 SQLite 트랜잭션으로 기록한다.

- 새 요청은 `QUEUED`, 0%다. 별도 작업 실행기를 실행하면 `VALIDATING`으로 선점된다.
- 배치 콜백은 `VALIDATING` 1%, `ANALYZING` 10~85%, `RENDERING` 90%, 성공 시 100%를 보고한다. 이 비율은 작업 시간의 예측치가 아니라 처리 단계의 표시값이다.
- `total_frame_count`는 요청 구간에서 분석할 프레임 수이며, `processed_frame_count`는 유효하여 분석한 프레임 수다. 빈 프레임은 전체에는 포함되지만 처리 수에서는 제외된다.
- 진행률과 처리 프레임 수는 같은 시도 안에서 감소할 수 없다. 완료·실패 상태는 후속 갱신할 수 없다.
- 단일 작업 실행기 재시작 시 진행 중이던 작업은 `WORKER_INTERRUPTED` 오류로 실패 처리한다. API 서버 시작은 실행 중 작업 상태를 변경하지 않는다. 대기 작업은 대기 상태로 남는다.

## 실패와 재시도

- 최대 시도 횟수는 3회(최초 1회와 재시도 최대 2회)다.
- `INPUT_OUTPUT_ERROR`, `DOWNLOAD_FAILED`, `UPLOAD_FAILED`, `ANALYSIS_TIMEOUT`, `WORKER_INTERRUPTED`만 재시도 가능하다. 입력 오류는 재시도하지 않는다.
- 재시도 시 같은 `job_id`를 유지하고 상태·진행률을 `QUEUED`·0%로 초기화한다. 시도 횟수는 DB에 보관한다.
- 작업 실행기는 재시도 가능 오류를 자동 재대기시키며 5초, 10초 지연 후 다시 선점한다. 세 번째 실패는 `FAILED`로 남긴다.
- INT-001은 완료된 실제 작업의 결과 JSON을 반환하고 후보 PNG는 같은 API의 이미지 경로로 제공한다. 미완료 작업은 `RESULT_NOT_READY`다.
