# energy 서버 - VTI 시험 API 배포 현황

기준: 2026-11-10

## 서버 정보

- 사양: **4 vCPU / 메모리 4 GB / 디스크 32 GB**
- 접속: `daonhns.iptime.org:8282` (SSH, 계정 `energy`)
- 서버 주소: 외부 접속 경유 `192.168.0.50`, Ubuntu 실제 인터페이스 `10.1.2.2/16`
- Docker: 설치 및 Compose 실행 확인
- 기존 개방 포트:
  - `8280`: 개방, FE 권장 포트. 이번 분석 API에는 미사용
  - `8281`: 개방, BE 권장 포트. 이번 분석 API에는 미사용
  - `8282`: 개방, SSH 접속용
  - `8283`: 미개방, DB 권장 포트. 이번 작업에서 개방하지 않음

## 배포 결과

- 담당 범위: **INT-003 작업 접수 / INT-004 상태·진행률 / INT-001 결과 JSON·PNG**
- 공개 시험 API: **`https://heatpipe.daonhns.com` (HTTPS 443)**
- 분석 서버: Docker Compose로 **API 컨테이너 + CPU 분석 워커 컨테이너** 실행
- API 포트: Ubuntu `10.1.2.2:18001` → API 컨테이너 `8000`
- 작업 상태·결과: API와 워커가 `heatpipe_data` Docker 볼륨 공유; SQLite 작업 상태와 결과 JSON·PNG 저장
- HTTPS 인증서: win-acme로 `heatpipe.daonhns.com` 인증서 발급, 갱신 항목 생성
- 현재 미사용: 분석 서버의 Caddy HTTPS 컨테이너는 시험 후 중지

## 외부 접속 경로

```text
heatpipe.daonhns.com
→ 공인 IP 211.60.149.76
→ ipTIME 외부 443
→ 삼성 컴퓨터 192.168.0.185의 nginx(HTTPS 처리)
→ 삼성 컴퓨터의 HeatpipeApiTunnel Windows 서비스(SSH 포워드)
→ Ubuntu 10.1.2.2:18001
→ Docker API 컨테이너
```

- 기존 공유기 80·443 규칙이 삼성 컴퓨터를 가리켜 nginx에서 새 도메인을 처리하도록 설정
- 삼성 컴퓨터에서 Ubuntu의 `10.1.2.2`로 직접 연결되지 않아 자동 시작 SSH 터널 서비스를 사용
- 개인 PC에서 연 SSH 터미널을 닫아도 Docker API·워커와 Windows 터널 서비스는 계속 실행

## 검증 결과

- 외부 HTTPS 인증서, `/health`·`/version` 응답, 문서 경로 404 및 무인증 작업 조회 401 확인
- 휴대전화 모바일 데이터에서도 공개 `/health` 응답 확인
- **2026-11-10 재확인:** 공개 `/health` HTTP 200, `worker_running=true`, 대기 작업 0건
- 합성 ATT·ATG 4프레임: INT-003 `202` 접수 → INT-004 `SUCCEEDED`·100% → INT-001 후보 1건·PNG 1건 조회
- 후보 PNG의 바이트 크기·SHA-256이 결과 JSON과 일치
- API·워커 재시작 후에도 작업 상태와 결과 JSON 유지

## VTI 연동 설정

- 분석 워커의 일반 HTTPS 외부 출발 IP: **`211.60.149.76`**
- VTI 시험용 MinIO 호스트: `daon-hs.stg.vti.com.vn`
- 허용 목록: 서버 `.env`에 위 호스트를 등록하고 API·워커를 재생성; 두 서비스 정상 실행 확인
- VTI에 2026-11-06 API 명세와 시험 주소 전달; VTI가 주소 수신을 회신으로 확인
- Bearer 키 수신 담당자·이메일 전달 가능 여부를 VTI가 회신. 11월 10일 VTI 시험망의 HTTPS 접속·Bearer 인증 완료 회신을 받음

## 남은 확인

- VTI 시험망에서 INT-004 상태·INT-001 결과 JSON·후보 PNG 조회
- VTI의 실제 Presigned HTTPS URL을 이용한 ATT·ATG 다운로드 및 전체 파일 분석
- 실파일 처리 시 4 GB 메모리·32 GB 디스크 여유, 처리 시간 및 결과 보관 용량
- API·워커 내부의 허용 호스트 설정값, VTI MinIO 접근 허용 결과
- Windows 재부팅·연결 끊김 후 SSH 터널 복구, 인증서 갱신 후 nginx 자동 reload
- 현재 사용하지 않는 공유기 중복 443 포트포워딩 규칙 정리. 기존 서비스용 80·443 규칙은 유지

비밀번호, Bearer 키 값, SSH 개인키와 Presigned URL 원문은 이 문서에 포함하지 않는다. 상세 절차는 `docs/DOCKER_DEPLOYMENT.md`, 확인 기록은 `docs/TEST_ENDPOINT_READINESS_2026-11-09.md`를 참조한다.
