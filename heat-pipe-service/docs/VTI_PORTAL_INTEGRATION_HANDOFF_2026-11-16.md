# VTI 포털 ↔ 분석 API 연결: 11월 16일 이어서 할 작업

기준: 2026-11-16 오전. VTI의 11월 13일 추가 요청을 반영한 작업 메모다. 사용자 요청에 따라 포털 설치 전에 서버 설정을 적용했다. **포털 컨테이너와의 실제 통신은 아직 검증 전이다.**

## 현재 상태

- Ubuntu 시험 서버에는 분석 API·워커가 실행 중이다. 기존 공개 HTTPS 주소 `https://heatpipe.daonhns.com`은 별도 Windows nginx와 SSH 터널을 거쳐 API로 연결된다.
- 서버의 `heatpipe-shared` 네트워크가 존재하며, 분석 API·워커를 기존 `default`와 공유 네트워크에 함께 연결하도록 `compose.portal.yaml`을 적용했다. API에 `daon-api` 별칭을 설정했다. 포털 컨테이너에서의 접속은 아직 확인하지 않았다.
- 기본 `compose.yaml`은 두 서비스가 동일한 `heatpipe_data` 볼륨을 `/data`에 마운트한다. `/data` 공유를 위한 추가 Compose 수정은 없다.
- 로컬 `storage/backends.py`는 HTTP 허용 목록의 `portal-minio:9000`처럼 **호스트:포트** 값을 지원하도록 수정했다. 기존 호스트명만 적은 허용 값도 계속 동작한다.
- 서버 `.env`의 내부 HTTP 허용 값이 비어 있음을 확인한 뒤 `portal-minio:9000`으로 설정했다. 준비 파일 2개만 전송하고 이미지 재빌드·API/워커 재생성을 완료했다. API는 `healthy`, 워커는 실행 중이고 공개 HTTPS `/health`가 정상 응답했다. 로컬에 다른 미커밋 변경이 있으므로 전체 소스 폴더를 무심코 서버로 복사하지 않는다.
- 서버의 API·워커는 `/data`에 동일한 **`heatpipe-service_heatpipe_data` 볼륨**을 마운트한 것을 확인했다. VTI 예시의 `heatpipe-data`로 볼륨 이름을 바꾸지 않는다. 기존 이름과 데이터를 유지한다.

## 11월 16일 적용 기록 및 확인 순서

1. `heatpipe-shared` 네트워크가 이미 생성된 것을 확인했다. 공개 API와 Docker 상태도 확인했다.
2. 재생성 직전 SQLite의 활성 상태 조회 결과가 `[]`였다. `/health`의 대기 건수만으로 실행 중 작업까지 확인할 수 없으므로 아래 명령을 사용했다.

   ```sh
   docker compose -f compose.yaml -f compose.https.yaml exec -T api python -c 'import sqlite3; print(sqlite3.connect("/data/jobs.sqlite3").execute("select status,count(*) from jobs where status in (?,?,?,?,?,?) group by status", ("QUEUED","VALIDATING","DOWNLOADING","ANALYZING","RENDERING","UPLOADING")).fetchall())'
   ```

3. 로컬의 `compose.portal.yaml`과 `src/heatpipe_service/storage/backends.py`만 압축해 서버로 전송했다. SHA-256을 대조한 뒤 기존 `backends.py`를 백업하고 압축을 풀었다. 서버 `.env`의 기존 내부 HTTP 허용 값은 비어 있었으며 `portal-minio:9000`을 설정했다. Bearer 키나 `.env` 전체를 채팅·문서에 복사하지 않았다.
4. 서버의 `~/heat-pipe-service`에서 Compose 설정을 검증하고 이미지를 다시 빌드한 후 API·워커만 재생성했다. 실제로는 빌드와 재생성을 별도 명령으로 실행했다. 중지된 `https` 컨테이너는 시작하지 않았다.

   ```sh
   docker network inspect heatpipe-shared
   docker compose -f compose.yaml -f compose.https.yaml -f compose.portal.yaml config --quiet
   docker compose -f compose.yaml -f compose.https.yaml -f compose.portal.yaml up -d --build --no-deps --force-recreate api worker
   docker compose -f compose.yaml -f compose.https.yaml -f compose.portal.yaml ps api worker
   ```

5. 공개 `https://heatpipe.daonhns.com/health`는 재생성 후 정상 응답했다. 분석 워커에서 `http://daon-api:8000/health`를 호출해 `status=ok`, `worker_running=true`를 확인했다. API·워커 양쪽에서 `portal-minio:9000` URL 허용 검사도 통과했다. 두 컨테이너의 `/data` 마운트가 모두 `heatpipe-service_heatpipe_data`임을 확인했다. VTI 포털 컨테이너에서 `http://daon-api:8000/health` 호출, 실제 ATT·ATG 다운로드와 INT-003 → INT-004 → INT-001 흐름은 설치 후 검증한다.

## 아직 확인되지 않은 것

- 포털 설치 후 포털 컨테이너의 `heatpipe-shared` 연결 상태.
- VTI 포털 컨테이너에서 `daon-api` DNS가 해석되고 API 인증 요청이 성공하는지. 현재는 분석 워커 컨테이너에서만 별칭 접속을 확인했다.
- 실제 포털 MinIO 서명 URL의 다운로드, 전체 실파일 분석 시 서버 메모리·디스크 여유.
- **포털 설치 후 권장 설정:** VTI의 전체 기능 시험 완료 안내 후 `compose.portal.yaml`에 API·워커의 `json-file` 로그 순환(`max-size: "10m"`, `max-file: "5"`)을 추가했다. 서버의 기존 파일을 백업하고 새 설정 파일로 교체했다. 활성 작업이 `[]`임을 재확인한 뒤 두 컨테이너를 재생성했다. `docker inspect`에서 두 서비스 모두 `restart=unless-stopped`, `log=json-file`, `max-size=10m`, `max-file=5`를 확인했다. 공개 분석 API `/health`와 포털 HTTPS 주소도 정상 응답했다.

로컬 검증: Compose 병합 문법 통과, `api`의 `daon-api` 별칭과 두 서비스의 기존 `default`·공유 네트워크 및 `/data` 볼륨 유지 확인, `portal-minio:9000` 허용 단위 시험 통과. 서버 연동 검증 결과로 해석하지 않는다.
