# Claude 재검토 요청: r3 요청 JSON의 null 생략

작성 2026-11-19. 사용자가 저장 방식 보완 후 재검토를 선택했다.
**로컬 수정·검증만 완료. 서버 코드/DB 미변경, 커밋·푸시 미실행.**
기존 r2 통과 부분은 유지하며, 이번 코드 변경은 JobStore의 JSON 직렬화 두 곳이다.

## 요청할 검토

신규 접수와 QUEUED 재전송의 request_json 저장에
`model_dump_json(exclude_none=True)`를 적용했다. API 요청/응답 모델과
OpenAPI는 변경하지 않았다. 아래 목적과 한계에 맞게 동작하는지 검토해 주세요.

1. 새 URL/object_key 요청에 path:null 등 불필요한 null 필드가 저장되지 않는지.
2. 중첩 frame_range.end_exclusive, analysis_roi 등 null 생략이 모델의 기본값 복원과
   입력 내용·동일 키 비교를 바꾸지 않는지. false/0/빈 값까지 제거하는 변경은 아니다.
3. QUEUED 재전송·path→URL 참조 갱신에서도 같은 직렬화가 적용되는지.
4. 새 URL/object_key 기록을 구버전 모델이 읽고, 대기/완료 작업의 같은 키 재전송을
   구버전 create가 기존 job_id로 반환하는지.
5. 기존 기록은 무작위로 변환하지 않고, 실제 path 값은 보존하는지.

## 변경 파일

- `src/heatpipe_service/jobs/store.py`: INSERT와 QUEUED UPDATE 두 직렬화에 exclude_none.
- `tests/unit/test_request_storage_rollback.py`: 10개 회귀 사례.
- `tests/fixtures/legacy_v1_request.py`: git 7e6137의 pre-path 요청 모델·create 메서드·
  비교 함수를 고정한 테스트 fixture. DB 연결/조회 등 공통 기반은 현재 JobStore를
  재사용한다. 실제 구버전 Docker 이미지 전체를 기동한 시험은 아니다.
- 내부 검토·이전 계획 문서와 검토 ZIP 생성 스크립트. 서비스의 다른 로직은 r2와 동일.

## 검증 결과

Linux Python 3.12에서 `python -m pytest tests/unit -q`: **87 passed**.
추가 회귀 10개:

- URL/object_key × QUEUED/SUCCEEDED: 새 기록의 구버전 파싱·동일 키 재전송 4개.
- URL/object_key QUEUED 위치 갱신 후 null 생략 2개.
- 실제 path 값 보존과 구버전 거부 1개.
- 이미 저장된 완료 기록은 읽히지만 자동 변환되지 않음 1개.
- QUEUED path→URL 전환 뒤 구버전 재전송 1개.
- 기존 null 포함 QUEUED 기록은 명시적으로 갱신할 때만 재직렬화 1개.

## 해결 범위와 남은 제한

- 새 URL/object_key 요청은 구버전이 모르는 path:null을 저장하지 않는다.
- **실제 path 입력은 구버전에서 여전히 불가능하다.** path가 non-null이므로 남는다.
- 이미 저장된 path:null 기록을 일괄 마이그레이션하지 않는다. 기존 완료 기록의
  동일 키 재전송은 구버전에서 여전히 실패할 수 있다.
- 롤백은 신규 접수 중단·모든 대기/실행/자동 재시도 작업 정리·버전별 request_json
  점검·파일/DB 정합성 복구 절차를 유지한다. exclude_none만으로 롤백 안전성을
  보장했다고 판단하지 않는다. 실제 구버전 서버 이미지 복원 시험은 별도다.
- VTI에 보낸 API 명세 ZIP/PDF는 수정하지 않았다. 외부 계약 변경이 없는 내부
  저장 보완이고, 송부본의 보수적인 대기열 정리 조건도 계속 준수한다.
- 배포 전 코드 커밋/태그 고정과 미커밋 알고리즘·실험 코드 분리, 서버 백업/복원
  시험이 남아 있다. 이번 자료는 설치/배포 패키지가 아니다.

검토 결과는 결함의 심각도·근거·수정안과 함께 r3 통과 여부로 알려 주세요.
