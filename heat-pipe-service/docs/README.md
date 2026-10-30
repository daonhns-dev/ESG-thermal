# Heat Pipe Service 문서 기준

`heat-pipe-service/docs`는 VTI 연동 및 운영 서비스 구현의 기준 문서 위치다.
기존 `heat-pipe/docs`는 PoC 실험 기록과 과거 협의 이력을 보존하는 용도로만
사용한다.

## 기준 문서

| 문서 | 상태 | 용도 |
|---|---|---|
| [`INT_001_003_004_DEVELOPMENT_SCOPE.md`](INT_001_003_004_DEVELOPMENT_SCOPE.md) | 현재 범위 기준 | VTI–DaonH&S 개발 책임과 연동 범위 |
| [`BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md`](BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md) | 내부 구현 초안 | 배치 분석 흐름과 결과 JSON 정책 |
| [`schemas/analysis-result-v0.schema.json`](schemas/analysis-result-v0.schema.json) | 내부 검토용 v0 | 분석 결과 JSON Schema |
| [`examples/analysis-result-v0.example.json`](examples/analysis-result-v0.example.json) | 협의용 예시 | VTI·PM 검토용 성공 결과 예시 |
| [`CORE_MIGRATION.md`](CORE_MIGRATION.md) | 완료 기록 | PoC 이상탐지 코어 이관 및 검증 내역 |

## 문서 변경 원칙

- 외부 연동 계약의 변경은 범위 문서와 API 명세에 함께 반영한다.
- 확정되지 않은 사항은 `제안` 또는 `확정 필요`로 표시한다.
- INT-002·INT-005 및 GIS/SHP 관련 과거 문서는 구현 기준으로 사용하지 않는다.
- 2026년 11월 6일 전달본부터 엔드포인트·요청·응답·인증·오류 코드를
  OpenAPI 명세와 일치시킨다.

