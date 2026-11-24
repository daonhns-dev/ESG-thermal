# r3 배포 준비 및 운영 검증 기록

## 후속 운영 배포 완료 — 2026-11-23

이 기록이 아래 준비 단계의 미배포 표기보다 우선한다. VTI와 작업 시작을 합의한 뒤
deploy-r3-url.sh를 실행했고 R3_URL_DEPLOY_SUCCESS 출력을 확인했다.

- 배포 직전 활성 작업 없음. 현재 데이터 SQLite 검사·오프라인 백업 후 이미지 교체.
- DEPLOY_STATE: `/data/data/backups/r3-deploy-20261123-JB04AR`.
- 새 실행 이미지: r3-20261119-build2, ID 5e007f46e185…로 스크립트가 확인.
- 배포 전후 DB count/max(id)/max(job_id): 47 / 47 / JOB-20261123-000047로 동일.
- 기존 결과 JOB-20261123-000046과 PNG 50개 크기/SHA-256 확인.
- API healthy, worker_running=true, queued_job_count=0.
- /data는 기존 /data/data/analysis bind 유지, path 모드는 비활성화.
- 포털·Docker 엔진·서버 재시작은 이 배포 스크립트에서 수행하지 않았다.
- 중지된 Caddy의 orphan 경고는 예상된 것이며 삭제하지 않았다.
- 배포 당시에는 검증 후보 manifest와 이미지 라벨로 소스를 추적했다. 이후 기능 커밋 `5a2330e`의 패키지 파일 26개가 후보 manifest의 SHA-256과 일치함을 확인했다.
- 후속: Docker 재시작 전 자동 재시작 정책/실행 목록 확인, 합의된 시험과 외부 응답 확인.

### Docker 재시작 시험 실행 완료 — 최종 상태 확인 대기

VTI와 합의한 중단 시간에 실행했다. 직전 활성 작업은 없었고 포털 4개/분석 2개 모두
restart=unless-stopped, live_restore=false를 확인했다. /data mountpoint 및 유닛 검사를
통과한 뒤 sudo systemctl restart docker를 실행했고 systemctl은 active였다.

재시작 8초 후 6개 컨테이너 모두 Up. 분석 API, 포털 API, PostgreSQL은 healthy,
포털 frontend와 MinIO는 health: starting이었다. 분석 /health는 status=ok,
worker_running=true, queued_job_count=0. DOCKER_RESTART_COMMAND_COMPLETE 확인.
기존 중지 Caddy는 계속 Exited이며 의도한 상태다. 서버 전체 재부팅은 하지 않았다.

Docker 재시작 시 ExecStartPre가 있는 설정으로 정상 시작함은 확인했다.
/data를 해제한 실패 시나리오는 시험하지 않았다. frontend/MinIO healthy 전환과
공개 API/포털 응답, VTI 측 접속 확인은 후속 결과 수신 전까지 미완료다.

### 후속 최종 확인 — 서버 측 통과

사용자 후속 출력에서 포털 frontend/portal-api/PostgreSQL/MinIO 및 분석 API 모두
healthy, worker Up을 확인했다. 공개 https://heatpipe.daonhns.com/health는 status=ok,
worker_running=true, queued_job_count=0이며 공개 포털은 PORTAL_HTTP=200.
따라서 위 frontend/MinIO 및 공개 응답 대기 항목은 해소됐다.
현재 r3 URL 모드 운영 배포와 Docker 재시작/자동 복구의 서버 측 시험은 완료다.
실제 VTI 측 로그인/정상 파일 신규 분석 확인, 공유 원본 path 전환,
Git 커밋 고정, 호스트 전체 재부팅 시험을 완료한 것으로 확대하지 않는다.

### 후속 공유 원본 PATH 설정 활성화 — 2026-11-23

VTI가 포털 PATH 전환과 원본 표식 준비 완료를 알린 뒤 진행했다.
활성 작업 없음 확인, UID/GID 10001 계정의 표식 및 원본 샘플 2개 읽기 검사 통과.
enable-shared-input.sh는 검토한 compose.shared-input.yaml과 같은 오버레이를 추가했다.
기존 설정 대비 공유 루트 환경변수와 읽기 전용 마운트만 추가됨을 병합 비교로 확인했다.

- API·워커 모두 /data/data/portal/raw -> /shared-input (read_only=true).
- API·워커 HEATPIPE_SHARED_INPUT_ROOT=/shared-input.
- /data/data/analysis -> /data와 기존 이미지/네트워크/포트 설정 유지.
- COMPOSE_FILE 기본 목록에 compose.shared-input.yaml 추가.
- 설정 백업과 최신 오프라인 데이터 백업:
  /data/data/backups/shared-input-enable-20261123-LEKSA5.
- API·워커만 재생성, 두 컨테이너 각각 마운트 RW=false 및 공유 루트 읽기 검사 통과.
- DB 전후 48건 / max(id)=48 / JOB-20261123-000048로 동일.
- health status=ok, worker_running=true, queued_job_count=0.
- SHARED_INPUT_ENABLE_SUCCESS 확인. 포털 및 Docker 엔진 재시작은 하지 않았다.

위 URL-only/공유 경로 미활성 기록은 이전 단계 상태다. 현재는 공유 경로 설정이 활성화됐고
기존 URL 방식도 코드상 유지된다. 실제 VTI PATH 접수/분석 완료/결과·PNG 통합 시험은
후속 결과 수신 전까지 미확인이다. 이후 실제 path 기록이 생성되면 구버전 롤백 호환 범위가
달라지므로 URL-only 구버전 롤백 검증 결과만으로 단순 이미지 원복하지 않는다.

### VTI 배포 후 읽기 전용 종합 점검 — PATH 실작업 통과

사용자가 실행한 audit-after-portal-deploy.sh 출력(2026-11-23 07:47 UTC 무렵) 기준.
VTI 배포 후 포털 API/frontend 이미지가 바뀐 상태에서 점검했으며 우리 측은 이 점검으로
서비스 재시작·설정 변경·신규 분석을 수행하지 않았다.

- 분석 API 및 포털 frontend/portal-api/PostgreSQL/MinIO healthy, worker Up.
- 분석 두 컨테이너는 검증한 5e007f46e185… 이미지, restart=unless-stopped.
- 분석 /data는 /data/data/analysis RW, /shared-input은 /data/data/portal/raw RO.
- 포털 API는 같은 원본 폴더를 RW로 연결. 포털 PostgreSQL과 MinIO도 각각
  /data/data/portal/db, /data/data/portal/minio에 연결된 것이 확인됐다(VTI 적용분).
- API·워커 표식 검사 및 worker -> http://daon-api:8000/health 통과.
- DB quick_check=ok, 작업 49건(성공 16/실패 33), queued_job_count=0.
- 최신 PATH 작업 JOB-20261123-000049는 ATT/ATG 모두 path 방식,
  SUCCEEDED 100, processed/total=3481/3481.
- 해당 ATT 769,941,512 bytes, ATG 135,759 bytes가 공유 폴더에 남아 있으며
  API 계정에서 읽기 및 요청 크기 일치 확인. 이 점검에서는 대용량 원본 SHA를 재계산하지 않았다.
- 결과 JSON 조회 및 후보 PNG 50개 모두 image/png, PNG 시그니처, 크기/SHA-256 확인.
- 토큰 없는 보호 API 요청은 HTTP 401로 차단.
- 공개 API health ok/worker_running=true, 공개 포털 HTTP 200.
- /data ext4 정상 마운트, 251G 중 약 237G 가용. 루트 32G 중 약 23G 가용.

따라서 위 실제 VTI PATH 접수/완료/결과·PNG 대기 상태는 최신 작업에 대해 해소됐다.
JOB-000048의 공유 루트 오류는 설정 적용 전 실패이며 이후 PATH 성공으로 해당 설정 문제의
해소를 확인했다. 나머지 실패 33건 전체를 복구/해결한 것으로 해석하지 않는다.
최근 일부 URL 입력은 95/106바이트 수준 ATT 또는 이미 조사한 비정렬 ATT로 실패했다.

남은 확인은 VTI 포털 UI에서 로그인/업로드/결과 표시 확인 및 업무 시나리오 시험,
기존 전체 원본/포털 기록 이전의 완전성, 호스트 별도 백업 범위, Git 커밋 고정 등이다.
이 출력만으로 UI 검수·모든 과거 데이터 이전·호스트 재부팅까지 완료됐다고 판단하지 않는다.

## 범위와 순서

실제 순서는 기존 이미지 유지 → 분석 데이터 백업/복원 검증 → 분석 저장소 이전 완료
→ r3 이미지 준비 → 협의한 시간에 URL 모드 코드 교체 → VTI 준비 후 path 활성화다.
이번 준비는 이미지 생성용 자료와 절차까지이며 서비스/엔진 재시작은 하지 않는다.

## 포털 설정 복구

2026-11-19 서버 실행 결과 복구 완료. 백업:
`/home/energy/heatpipe-portal/docker-compose.prod.yml.bak-mount-20261119-061427-049584`.
두 포털 Compose 파일의 병합 검사에서 `minio -> heatpipe-portal_minio-data` PASS.
컨테이너 재배포는 하지 않았다. 아래는 수행한 복구의 범위다.

실행 중 MinIO는 기존 named volume을 사용한다. 미적용 Compose에 남은
`/data/data:/data` 한 줄을 원래 `minio-data:/data`로 복구하면 된다.
VTI 수정본일 수 있으므로 파일 백업과 현재 minio 서비스의 volumes 확인 후 그 줄만
수정한다. 포털 파일 전체를 예전 버전으로 덮어쓰지 않고 재배포하지 않는다.
수정 후 VTI가 사용하는 전체 Compose 목록으로 병합하여 기존 named volume을
가리키는지 확인한다. 명령 출력 확인 전에는 복구 완료로 기록하지 않는다.

## 중단 없는 서버 사전 점검

```bash
test -x /usr/bin/mountpoint && /usr/bin/mountpoint -q /data && echo MOUNTPOINT_OK
systemctl show docker.service --no-pager -p ExecStartPre -p RequiresMountsFor -p After
systemd-analyze verify docker.service
systemctl list-dependencies docker.service --plain --no-pager | grep data.mount
docker inspect heatpipe-portal-frontend-1 heatpipe-portal-portal-api-1 heatpipe-portal-postgres-1 heatpipe-portal-minio-1 --format '{{.Name}} restart={{.HostConfig.RestartPolicy.Name}}'
```

verify는 관련 유닛 경고도 출력할 수 있다. 오류 내용을 확인하며, 성공만으로 실제
재기동 성공을 보장하지 않는다. Docker 재시작/재부팅 시험은 VTI와 합의한 시간에 한다.

서버 조회 결과: mountpoint 실행 및 /data 검사 성공, ExecStartPre/RequiresMountsFor 설정
확인, systemd-analyze verify 출력 없음. 다만 list-dependencies에서 data.mount가
표시되지 않았다. 후속 show에서 After=data.mount는 확인됐고 Requires에는 data.mount가
없었다. 시작 순서와 ExecStartPre 방어는 확인했으나 실제 재기동은 미검증이다. 기존 이미지에는 setuptools
79.0.1, wheel 0.46.3이 있다. 실제 후보 이미지 빌드는 아직 미실행이다.

## 빌드 전용 묶음

`output/analysis-r3-build-kit-20261119-build2.tar.gz`는 검토/테스트된 후보에서
src, pyproject.toml, README와 전용 Dockerfile/빌드 스크립트만 담는다.
후보 manifest, 87개 단위 테스트 검증 기록, BUILD_SHA256SUMS를 함께 담는다.
서버의 Compose, .env, 운영 데이터는 포함하지 않는다. 별도 빈 폴더에 풀어야 하며
운영 소스 폴더에 덮어 풀지 않는다. 커밋/태그 생성은 아직 하지 않았다.

빌드는 보존 이미지 ID가 기존 실행 이미지와 맞는지 검사하고 네트워크 없이
`pip install --no-deps --no-build-isolation --no-index .`를 실행한다.
setuptools/wheel이 없으면 중단한다. 이 경우 새 빌드 도구를 자동 설치하지 않는다.
생성 이미지와 기존 이미지의 설치 배포판 이름/버전을 비교한다(서비스 패키지 제외).
기존 local 태그와 실행 컨테이너는 빌드 스크립트가 변경하지 않는다.
컨테이너 의존성 검증/기본 import만으로 실제 분석과 golden 검증을 대체하지 않는다.

### 첫 빌드 실패와 build2 보완

첫 빌드 경로: `/home/energy/heatpipe-r3-build.VNhHIK`.
`heatpipe-service:r3-20261119-candidate` 이미지는 생성됐지만 path 모델 검사에 실패했다.
`/app/src`의 models SHA는 새 후보인 반면 `/app/build/lib`와 실제 site-packages의 SHA는
구버전이었다. 기반 이미지의 기존 build 산출물과 아카이브의 오래된 mtime으로 인해
구버전 패키지가 설치됐다. 이 이미지는 배포 대상이 아니며 local 태그는 변경하지 않았다.

build2는 새로운 `/opt/heatpipe-r3-build2`에서 force-reinstall하며 의존성은 그대로 유지한다.
생성 태그는 `heatpipe-service:r3-20261119-build2`로 구분한다. 설치된 패키지 전체의
파일 목록/바이트 SHA-256을 원래 후보 manifest와 대조한 뒤 path/import를 검사한다.
앱 소스는 변경하지 않았다.

build2 서버 빌드 완료: `/home/energy/heatpipe-r3-build2.HyQFAo`.
이미지 ID: `sha256:5e007f46e1856316d7c8c45cc715693955b43505cb3dd34c41d93decb08c7948`.
후보 manifest SHA: `ac6c9dafa140bb1cccaab78a9a32db0e8b051aef8e43fd06d95ee0e5d6a10626`.
설치된 패키지 26파일 SHA 일치, 의존성 버전 동일, path 모델/worker import 확인.
BUILD_ONLY_COMPLETE 출력 확인. 운영 local 태그와 실행 서비스는 변경하지 않았다.
격리 복원본 API/워커 기능 시험까지 완료했다(아래 기록). 운영 배포는 미실행이다.

### 격리 기능 검증 완료

서버 검증 경로: `/data/data/backups/r3-image-validation-yguRmU`.
기존 백업 복사본만 /data에 연결하고 network none, 포트 미공개로 새 이미지를 실행했다.
실제 worker 명령을 별도 프로세스로 실행하고 컨테이너 loopback HTTP에서 합성 원본을
받도록 했다. 운영 서비스·이미지 태그·현재 운영 데이터는 변경하지 않았다.

- 기존 JOB-20261119-000027 결과와 PNG 50개 크기/SHA-256 확인.
- 합성 URL 작업 JOB-20261119-000028: SUCCEEDED 100, PNG 1개 크기/SHA-256 확인.
  이 ID는 격리 복사본에서 생성됐으며 운영 작업 ID가 아니다.
- 같은 Idempotency-Key 재요청은 동일 job_id 반환.
- 저장 JSON의 null path 제외, 합성 원본 보존 확인.
- path 요청은 SHARED_INPUT_NOT_CONFIGURED로 거부되어 비활성 상태 유지.
- ISOLATED_VALIDATION_PASS, ISOLATED_CHECK_EXIT=0 확인. 임시 컨테이너 제거 완료.

이는 실제 VTI MinIO 연결이나 실측 전체 파일 성능 시험을 대체하지 않는다.

## 새 이미지 검증과 실제 적용 — 별도 작업

### A/B 단계 구분

A 단계는 VTI 포털 수정 완료와 무관하게 중단 시간 합의 후 진행할 수 있다.
커밋/배포 버전 고정과 실제 구버전 이미지 롤백 검증 후 r3를 URL 모드로 배포한다.
기동 순서 확인 및 Docker/서버 재시작 시험은 path 활성화 전에 별도 확인한다.
Docker 전체 재시작은 포털에도 영향을 주므로 VTI와 시간을 합의해야 한다.
/data 마운트가 없으면 ExecStartPre로 Docker 전체(포털 포함)가 시작되지 않는
동작임을 운영 협의 시 공유한다. 마운트를 해제해서 시험하지 않는다.

B 단계는 VTI 준비 후 호스트 스냅샷/백업 범위 확인과 실행을 선행 조건으로 두고,
원본 게시·권한·표식·공유 경로 연결·실제 업로드 통합 시험을 진행한다.
호스트 스냅샷이 불가능한 경우 동등한 복구 수단을 담당자와 합의하기 전 이전하지 않는다.

1. 사용자 지시에 따라 검토한 소스를 커밋/버전으로 고정한다. manifest와 차이를 확인한다.
2. 빌드 묶음 해시를 확인한 뒤 별도 폴더에서 빌드만 수행한다.
3. 기존 데이터의 격리 복원본으로 새 이미지의 API/워커와 URL 분석을 검증한다.
   운영 DB/원본을 테스트 컨테이너에 쓰기 가능하게 마운트하지 않는다.
4. VTI와 중단 시간 합의 후 활성 작업 없음 확인 → 접수 차단/분석 정지 → 최신 데이터
   백업 → 작업 건수와 max(id)/max(job_id) 기록 → 새 이미지로 교체한다.
   새 path 모드는 아직 활성화하지 않는다.
5. 서버 .env, Compose, 기존 소스는 보존한다. 일반 Dockerfile/Compose를 후보에서
   덮어쓰지 않는다. 새 이미지의 고유 태그와 ID를 기록한 뒤 local 태그로 지정한다.
6. 실행 전에 `docker compose config --quiet`와 선택 항목만 출력하는 검사를 한다.
   API·워커 image가 새 이미지, /data가 같은 /data/data/analysis bind,
   공유 네트워크/daon-api/18001/로그 설정 유지, shared-input 비활성인지 확인한다.
7. `docker compose up -d --no-deps --no-build api worker`로 적용한다.
   --build, --remove-orphans, down -v를 사용하지 않는다.
8. 이미지 ID, health/worker heartbeat, 기존 결과/PNG, 신규 URL 작업을 확인한다.

## 롤백

URL 모드의 r3 exclude_none 저장은 구버전 호환을 보완하지만, 진행 중 작업과 신규
기록을 무시한 롤백은 하지 않는다. 접수 중단·작업 정리·현재 DB 백업 후 판단한다.
코드만 롤백할 경우 preserved pre-storage 이미지 ID를 다시 확인하고 local로 지정한
뒤 동일한 외부 analysis bind를 유지하며 `up --no-build`한다.
실제 path 기록이나 기존 null 포함 기록의 호환성은 별도 확인한다.
롤백 후 배포 직전 작업 건수/최대 ID와 결과 보존을 대조한다. r3-20261119-build2
태그는 삭제하지 않고 보존한다. 검증 스크립트 validate-r3-rollback.sh는 운영 데이터가
아닌 격리 검증 데이터의 추가 복사본을 실제 pre-storage 이미지로 읽고 같은 키를 재전송한다.
실행 결과 수신 전에는 롤백 실검증을 완료로 기록하지 않는다.

### 실제 구버전 이미지 롤백 실검증 완료 (2026-11-19)

후속 결과로 위의 실검증 대기 상태를 해소했다. 검증 경로:
`/data/data/backups/r3-rollback-validation-cd4XoG`.
r3 격리 데이터의 추가 복사본에 실제 보존 이미지 bc8e9b3395a9…를 연결했다.
network none, 공개 포트 없음, worker 미실행이며 운영 데이터/태그/서비스 변경은 없다.

- 실제 구버전 모델에 path 필드가 없음을 확인.
- r3 합성 URL 작업 JOB-20261119-000028의 SUCCEEDED 상태와 결과/PNG 1개 무결성 확인.
- 기존 요청 JSON과 같은 Idempotency-Key를 재전송해 같은 job_id 반환.
- 작업 28건과 최대 작업 ID가 재전송 전후 동일함을 확인.
- REAL_OLD_IMAGE_ROLLBACK_PASS, ROLLBACK_CHECK_EXIT=0 확인, 검증 컨테이너 제거.

이는 r3에서 생성한 URL 방식 기록에 대한 실제 구버전 호환 검증이다. path 방식 기록이나
과거 null 포함 기록의 일반적인 롤백 호환성을 보장하지 않는다.

Docker 추가 조회 결과는 After에 data.mount, RequiresMountsFor=/data,
ExecStartPre=/usr/bin/mountpoint -q /data를 확인했다. Requires에는 system.slice,
docker.socket, sysinit.target만 있었다. 마운트 실패 시 Docker 전체를 막는 ExecStartPre는
유지하며, 실제 Docker 재시작/재부팅 시험은 VTI와 시간 합의 후 진행한다.

이전 named volume은 이전 시점 자료다. 신규 작업은 `/data/data/analysis`에만 있으므로
옛 볼륨으로 복귀하려면 서비스 중단 중 최신 DB/WAL/결과를 일관되게 복사·검증해야 한다.
keep-current-data 오버레이를 현재 기본 명령에 추가하지 않는다.

## VTI 원본 전환 전 필수

운영 데이터와 현재 백업은 같은 /data LV에 있다. 별도 장애 대비 백업으로 간주하지 않는다.
대규모 원본 이전 전에 호스트 담당자와 스냅샷/vzdump의 디스크 포함 범위 및 별도 저장 위치를
확인한다. 포털 원본 게시·권한·표식·ro 마운트와 실제 업로드 통합 시험은 VTI 준비 후 진행한다.
