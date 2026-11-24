# 2026-11-19 Q15 v1.1 방안 A 송부 준비 기록

상태: 파일 생성·로컬 검증 완료. **실제 메일 발송·서버 적용은 하지 않았다.**

## 송부본 재검토 반영

Claude 검토 후 3곳의 문구를 수정했다. 롤백은 v1.1의 모든 대기 작업(URL 포함)을
정리한 뒤에만 허용, 서버 경로 표는 양사 확인 후 확정할 제안 배치로 표시,
URL/object_key에서 무결성 정보 생략 시 내용 비교 한계와 null 비교값 규칙을 명시했다.
11월 24일 일정 문구는 변경하지 않았다. 서비스 코드/요청 저장 방식은 변경하지 않았다.

별도 검토: request_json 저장 시 exclude_none=True를 적용하면 신규 URL/object_key
요청에서 path:null이 빠져 구버전의 extra=forbid 충돌을 줄일 수 있다. 하지만 실제
path 요청, 이미 저장된 JSON, 구버전의 동일 키 재전송 처리까지 자동 호환되지는 않는다.
완전한 롤백 호환성을 주장하려면 기존 데이터 변환/대기열 정리/구버전 재전송 시험이
별도로 필요하다. 이번 송부본 수정에는 이 코드 변경을 포함하지 않았다.

## 송부 파일

- `output/VTI_API_v1.1_방안A_20261119_송부용.zip`
- 단독 PDF: `output/pdf/Q15_API_변경명세_v1.1_방안A_20261119.pdf`
- Markdown 원문: `docs/Q15_SHARED_PATH_V1_1_SEND_20261119.md`

ZIP은 PDF/Markdown, 전체 OpenAPI, path·URL·혼합 요청과 202/503 응답 예시,
합성 ATT·ATG, 전체 오류 목록, 기존 결과 Schema, 안내문, SHA256SUMS.json을 포함한다.
코드·Compose 적용 파일·실제 촬영 원본·.env·비밀키는 포함하지 않는다.

## 검증

- PDF 5페이지를 pypdfium2로 렌더링하고 전 페이지를 시각 검토했다.
- OpenAPI 본문은 docs/openapi.json과 동일하며 info의 계약 버전·제공일·미적용 상태
  확장 필드만 추가했다. 서비스 info.version을 계약 버전으로 덮어쓰지 않았다.
- 모든 JSON 예시를 현재 Pydantic 모델로 검증했다.
- ZIP 모든 파일 해시, 압축 무결성, 합성 ATT·ATG의 실제 크기·해시를 검증했다.
- Linux 로컬에서 ZIP을 풀고 동봉 path 요청을 API로 접수해 워커 처리했다.
  fetch 함수를 호출하면 실패하도록 한 상태에서 SUCCEEDED, 결과 JSON,
  후보 PNG 1장과 SHA-256, 처리 후 원본 보존을 확인했다. 서버 접속은 하지 않았다.
- 단위 테스트 77개 통과는 직전 r2 코드의 Linux 검증 결과다. 이번 변경은
  문서·패키지 생성 및 검증 스크립트이며 서비스 로직은 변경하지 않았다.

## 재생성·검증

Windows의 reportlab 및 맑은 고딕 폰트가 있는 Python으로
`scripts/build_vti_plan_a_20261119_package.py`를 실행한다.
Linux의 프로젝트 테스트 의존성이 있는 Python으로
`scripts/validate_vti_plan_a_handoff.py`를 실행한다.

실제 서버의 새 path 계약 제공 완료, 외부 스토리지 이전 및 VTI 포털 통합 시험은
별도 작업이다. ZIP은 개발 착수용 API 명세 송부본이며 배포 패키지가 아니다.
