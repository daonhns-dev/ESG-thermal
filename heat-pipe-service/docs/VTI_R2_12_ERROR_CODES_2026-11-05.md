# R2-12 오류 응답 형식과 코드 목록

HTTP 오류 본문은 모든 API에서 `application/json`의 `{"error":{"code":"...","message":"..."}}` 형식이다. 이미지 조회도 **성공 200만 `image/png`**이고 오류는 JSON이다. INT-004의 작업 상태 응답은 HTTP 200이며 작업 실패 시 `error`에 `code`, `message`, `retryable`을 포함한다.

## HTTP 오류

| HTTP | `error.code` | 발생 상황 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | Bearer 키 누락·불일치 |
| 404 | `JOB_NOT_FOUND` | 작업 ID 없음 |
| 404 | `IMAGE_NOT_FOUND` | 완료 작업에 해당 후보 PNG 없음 |
| 404 | `NOT_FOUND` | 정의되지 않은 API 경로 |
| 409 | `IDEMPOTENCY_KEY_CONFLICT` | 같은 키에 다른 입력 식별값 사용 |
| 409 | `CANCEL_NOT_ALLOWED` | 취소할 수 없는 최종 상태 |
| 409 | `RESULT_NOT_READY` | 미완료 작업의 결과 JSON·PNG 요청 |
| 422 | `INVALID_REQUEST` | 요청 JSON·헤더·경로 검증 실패 |
| 422 | `SOURCE_URL_NOT_ALLOWED` | URL 스킴·호스트가 배포 환경의 허용 목록에 없음(외부 HTTP 불허, 내부 HTTP는 별도 허용 호스트만 가능) |
| 503 | `AUTH_NOT_CONFIGURED` | 서버 인증키 미설정 |
| 503 | `RESULT_STORAGE_ERROR` | 결과 JSON·PNG 조회 실패 |

정의되지 않은 경로·메서드 등 프레임워크 오류도 같은 JSON 본문으로 정규화한다. 메서드 오류 405는 `METHOD_NOT_ALLOWED`, 예기치 않은 서버 오류 500은 `INTERNAL_SERVER_ERROR`다. 일반 503의 보조 코드 `SERVICE_UNAVAILABLE`도 예약되어 있다.

## INT-004 작업 오류

| `error.code` | 재시도 유형 | 발생 상황 |
|---|---|---|
| `INPUT_FILE_NOT_FOUND` | 아니요 | 입력 파일 없음 |
| `INVALID_ANALYSIS_REQUEST` | 아니요 | 분석 입력·ROI·설정 오류 |
| `INVALID_OBJECT_KEY` | 아니요 | 저장소 Object Key 형식 오류 |
| `FILE_INTEGRITY_ERROR` | 아니요 | 입력 파일 크기·SHA-256 불일치 |
| `SOURCE_URL_NOT_ALLOWED` | 아니요 | 워커 다운로드 시 URL 정책 위반 |
| `DOWNLOAD_FAILED` | 예 | URL·MinIO 다운로드 오류 |
| `UPLOAD_FAILED` | 예 | 결과 업로드 또는 PNG 재조회·검증 오류 |
| `INPUT_OUTPUT_ERROR` | 예 | 파일·디스크 입출력 오류 |
| `WORKER_INTERRUPTED` | 예 | 실행 중 워커 중단 감지 |
| `ANALYSIS_FAILED` | 아니요 | 기타 분석·워커 오류 |

`ANALYSIS_TIMEOUT`은 재시도 코드로 등록되어 있지만 현재 워커에 실행 시간 제한이 없어 자동 생성되지 않는다. `RESULT_NOT_FOUND`는 저장소 내부 코드이며 결과 조회에서는 HTTP 503 `RESULT_STORAGE_ERROR`로 변환된다. `RESULT_DELETE_FAILED`는 취소 정리 중 로그에 기록되며 작업 오류로 반환되지 않는다. `retryable=true`는 오류 유형을 뜻하며, 최대 시도 횟수를 소진한 뒤에도 true일 수 있다.
