# Heat Pipe Service 문서 기준

`heat-pipe-service/docs`는 VTI 연동 및 운영 서비스 구현의 기준 문서 위치다.
기존 `heat-pipe/docs`는 PoC 실험 기록과 과거 협의 이력을 보존하는 용도로만
사용한다.

## 기준 문서

현재 진행 단계와 남은 일은 [진행 현황](PROJECT_STATUS_2026-11-02.md)에 요약한다.

| 문서 | 상태 | 용도 |
|---|---|---|
| [`PROJECT_STATUS_2026-11-02.md`](PROJECT_STATUS_2026-11-02.md) | 현재 현황 | 완료된 로컬 구현·검증과 남은 연동·AI 모델 작업 |
| [`INT_001_003_004_DEVELOPMENT_SCOPE.md`](INT_001_003_004_DEVELOPMENT_SCOPE.md) | 현재 범위 기준 | VTI–DaonH&S 개발 책임과 연동 범위 |
| [`BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md`](BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md) | 내부 구현 초안 | 배치 분석 흐름과 결과 JSON 정책 |
| [`schemas/analysis-result-v0.schema.json`](schemas/analysis-result-v0.schema.json) | 내부 검토용 v0 | 분석 결과 JSON Schema |
| [`examples/analysis-result-v0.example.json`](examples/analysis-result-v0.example.json) | 협의용 예시 | VTI·PM 검토용 성공 결과 예시 |
| [`CORE_MIGRATION.md`](CORE_MIGRATION.md) | 완료 기록 | PoC 이상탐지 코어 이관 및 검증 내역 |
| [`openapi.json`](openapi.json) | 현재 Mock API 출력 사본 | 서버의 `/openapi.json`에서 생성되는 OpenAPI 명세 |
| [`JOB_STATE_V0.md`](JOB_STATE_V0.md) | 내부 구현안 | SQLite 작업 상태·진행률·재시도 정책 |
| [`STORAGE_WORKER_V0.md`](STORAGE_WORKER_V0.md) | 내부 구현안 | 로컬·MinIO 저장소와 단일 분석 워커 운영 |
| [`REAL_DATA_SMOKE_2026-11-02.md`](REAL_DATA_SMOKE_2026-11-02.md) | 검증 기록 | 실제 ATT·ATG 30프레임 단일 작업 결과 |
| [`REAL_DATA_FULL_2026-11-02.md`](REAL_DATA_FULL_2026-11-02.md) | 검증 기록 | 실제 ATT·ATG 전체 3,959프레임 배치 결과 |
| [`REAL_DATA_CENTER_ROI_2026-11-02.md`](REAL_DATA_CENTER_ROI_2026-11-02.md) | 검증 기록 | 하단 가운데 ROI로 전체 3,959프레임 재분석 |
| [`ROI_TEMPERATURE_FULL_2026-11-02.md`](ROI_TEMPERATURE_FULL_2026-11-02.md) | 검증 기록 | 대표 프레임 ROI 온도 통계 포함 전체 프레임 결과 |
| [`DOCKER_DEPLOYMENT.md`](DOCKER_DEPLOYMENT.md) | 배포 검증 기록 | API·분석 워커 Compose 실행과 HTTP 작업 검증, VTI 시험 서버 확인 항목 |
| [`GPU_ANALYSIS.md`](GPU_ANALYSIS.md) | 선택형 GPU 구현·검증 | CuPy 점수맵, CUDA 분석 워커 실행, CPU 결과 비교와 성능 |
| [`REAL_DATA_GPU_DOCKER_2026-11-02.md`](REAL_DATA_GPU_DOCKER_2026-11-02.md) | 검증 기록 | 실제 ATT·ATG 전체 3,959프레임 GPU Docker API 결과 |
| [`CPU_GPU_DOCKER_COMPARISON_2026-11-02.md`](CPU_GPU_DOCKER_COMPARISON_2026-11-02.md) | 비교 기록 | 같은 Docker 환경에서 실제 전체 프레임 CPU·GPU 시간과 결과 비교 |
| [`HTTPS_URL_LOCAL_TEST_2026-11-02.md`](HTTPS_URL_LOCAL_TEST_2026-11-02.md) | 검증 기록 | 로컬 서명 HTTPS URL의 실제 ATT·ATG 전체 다운로드·오류 처리 |
| [`Q15_VTI_INTEGRATION_API_DRAFT.md`](Q15_VTI_INTEGRATION_API_DRAFT.md) | VTI 협의용 초안 | Q15 연동 흐름·요청/응답·인증·오류·미확정 정책 |
| [`VTI_INTEGRATION_TEST_CHECKLIST.md`](VTI_INTEGRATION_TEST_CHECKLIST.md) | 시험 준비 목록 | 연동 시험 전 완료·합의·검증할 항목 |

INT-001·003·004의 제안 요청·응답 모델과 OpenAPI는 `src/heatpipe_service/api`에 구현되어 있다. 실행 방법은 [프로젝트 README](../README.md#api-및-openapi)를 참고한다. SQLite 작업 상태, 로컬·선택적 MinIO 저장소와 별도 분석 워커, 완료 작업의 JSON·PNG 조회, Bearer 인증이 구현되어 있다.

## 문서 변경 원칙

- 외부 연동 계약의 변경은 범위 문서와 API 명세에 함께 반영한다.
- 확정되지 않은 사항은 `제안` 또는 `확정 필요`로 표시한다.
- INT-002·INT-005 및 GIS/SHP 관련 과거 문서는 구현 기준으로 사용하지 않는다.
- 2026년 11월 6일 전달본부터 엔드포인트·요청·응답·인증·오류 코드를
  OpenAPI 명세와 일치시킨다.

