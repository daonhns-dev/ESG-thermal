# VTI 2차 검토 R2-02 회신 문안

요청하신 두 필드를 INT-001 결과 JSON에 추가했습니다.

- `processing.parameters.max_results`: 해당 작업에 적용한 반환 후보 상한입니다.
- `detected_candidate_count`: 최소 지속 프레임 기준을 통과한 후보 트랙의 상한 적용 전 건수입니다. 프레임별 원시 탐지 수나 확정 이상 지점 수는 아닙니다.

`candidate_count`는 실제 반환된 `candidates` 배열 길이입니다. 새 분석 결과에서 `detected_candidate_count > candidate_count`이면 상한 때문에 일부 후보가 결과에서 제외된 것입니다. 실패 작업과 이전 저장 결과는 전체 건수를 복원할 수 없어 `detected_candidate_count: null`로 표시합니다. 이전 저장 결과의 적용 상한도 확인할 수 없으면 `max_results: null`입니다.

결과 형식 버전을 `0.3.0`으로 올리고 JSON Schema·필드 설명서·OpenAPI·예시를 갱신했습니다. 2026-11-02 실데이터 50건 샘플은 당시 상한이 50이었다는 사실만 확인되므로 `detected_candidate_count`를 추정하지 않고 null로 두었습니다. 새 정책의 실데이터 전체 재실행 후 수치를 포함한 샘플을 다시 전달하겠습니다.
