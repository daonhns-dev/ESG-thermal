# VTI 연동 시험 준비 체크리스트

- 기준일: 2026-11-02
- 범위: [Q15 연동 API 명세 초안](Q15_VTI_INTEGRATION_API_DRAFT.md)과 [개발 범위](INT_001_003_004_DEVELOPMENT_SCOPE.md)의 INT-001·003·004
- `[x]`는 현재 확인된 사실, `[ ]`는 연동 시험 전에 완료하거나 양측이 확정할 항목이다.

## 완료·확인

- [x] FastAPI INT-003·004·001, PNG 조회, Bearer 인증, `/docs`, `/openapi.json` 구현.
- [x] SQLite 작업 ID·상태·진행률·오류 저장과 별도 단일 분석 워커 연결.
- [x] 로컬 저장소와 선택적 MinIO 저장소, 작업 후 임시파일 정리 구현.
- [x] 실제 ATT·ATG 첫 30프레임의 API 접수→작업 실행→상태→결과 JSON→PNG 단일 작업 검증. [기록](REAL_DATA_SMOKE_2026-11-02.md).
- [x] 실제 ATT·ATG 전체 3,959프레임의 배치 실행과 JSON Schema·CSV·PNG 산출물 검증. 약 20분 33초, 후보 50건. [기록](REAL_DATA_FULL_2026-11-02.md).
- [x] 2×3 격자 하단 가운데 기본 ROI로 전체 3,959프레임 재분석. 약 7분 45초, 모든 후보 좌표가 ROI 내부, PNG에 빨간 ROI 표시. [기록](REAL_DATA_CENTER_ROI_2026-11-02.md).
- [x] 후보 대표 프레임의 ATT 픽셀 온도로 ROI 전체 최저·평균·최고 및 최고−최저·최고−평균 계산. 전체 3,959프레임 재실행에서 50건 모두 기록·검증. [기록](ROI_TEMPERATURE_FULL_2026-11-02.md).
- [x] Q15의 파일 전송·완료 확인·진행률·후보 ID 기본안을 [명세 초안](Q15_VTI_INTEGRATION_API_DRAFT.md)에 문서화.
- [x] 로컬 WSL Ubuntu Docker Compose에서 API·분석 워커 배포, 영속 볼륨, 재시작 후 상태, 임시 파일 정리, 합성 4프레임 HTTP 작업 결과·PNG를 검증. [기록](DOCKER_DEPLOYMENT.md).
- [x] 실제 ATT·ATG 전체 3,959프레임을 GPU Docker API→분석 워커→결과 JSON·PNG로 검증. 50개 후보가 기존 CPU 배치 결과의 위치·온도·최대 이상점수와 일치. [기록](REAL_DATA_GPU_DOCKER_2026-11-02.md).
- [x] 동일 WSL Docker 환경에서 CPU 분석 워커로 전체 3,959프레임 재실행. GPU 95.227초, CPU 427.246초이고 후보 50건의 주요 값 일치. [비교](CPU_GPU_DOCKER_COMPARISON_2026-11-02.md).

## VTI 연동 시험 전 완료

- [ ] **파일 전달:** VTI가 실제 ATT·ATG를 MinIO에 업로드하고 각 파일의 Presigned HTTPS GET URL을 INT-003에 제공한다. DaonH&S 분석 워커에서 해당 호스트 접근 및 약 770MB ATT 다운로드를 시험한다.
- [ ] **URL 수명·무결성:** 대기 시간·다운로드 시간·자동 재시도까지 URL이 유효한지 확인하고 크기/SHA-256 제공 여부를 합의한다. URL 만료·잘못된 파일·접근 거절 때 INT-004 오류를 시험한다.
- [ ] **전체 구간 후보 품질:** API→분석 워커 경유 전체 구간과 PNG HTTP 조회는 확인했다. 차량 부근에 상위 후보가 집중되므로 후보 품질을 검토한다.
- [ ] **GPU 시험 서버 배포:** DaonH&S 시험 서버에서 GPU 모델·드라이버·Docker GPU 지원을 확인하고 `compose.gpu.yaml`로 API와 단일 CUDA 분석 워커를 가동한다. 영속 볼륨, 재시작 후 상태, 임시 파일 정리와 실제 입력 한 건을 서버에서 재확인한다. 로컬 Docker 검증은 완료했다.
- [ ] **접근망·인증:** 양측 HTTPS 기준 주소, 방화벽/허용 호스트, VTI 백엔드의 Bearer 키 보관·전달·교체, PNG 중계 경로를 정하고 호출한다. `/health`와 분석 워커의 가동 상태를 별도로 확인한다.
- [ ] **완료 확인:** INT-004 폴링 주기·최대 대기 시간을 합의하고 `QUEUED`→`SUCCEEDED` 및 최종 `FAILED` 화면 처리를 시험한다. 현재 콜백은 구현되어 있지 않다.
- [ ] **식별자·재요청:** VTI의 `monitoring_data_id` 형식, `job_id` 저장, HTTP 응답 유실 시 중복 POST 방지, 재분석 시 후보 ID 매핑 정책을 합의한다. 현재 동일 요청의 중복 접수 방지는 없다.
- [ ] **결과·보관:** VTI가 INT-001 JSON과 상대 PNG 경로를 Bearer 인증으로 읽고 후보 ID를 유지하는지 확인한다. 결과 보관기간·삭제/재조회 정책을 합의한다.
- [ ] **양측 통합:** VTI 백엔드에서 정상 한 건과 입력 오류·다운로드 실패·분석 실패·결과 미준비 시나리오를 실행하고 OpenAPI·Q15 문서의 최종 차이를 반영한다.

`object_key`만 전달하는 방식이나 DaonH&S의 결과 MinIO 업로드를 선택할 때에만 공유 저장소 권한·실제 MinIO 서버 검증 항목을 추가한다. GIS/SHP, INT-002·005는 이 체크리스트 범위 밖이다.
