# DaonH&S 분석 서비스 Docker 배포·검증

기준일: 2026-11-02. 이 Compose는 DaonH&S의 **분석 API와 단일 분석 워커**를 배포한다. VTI 포털, 포털 DB, VTI의 MinIO는 포함하지 않는다. 현재 구성은 SQLite와 로컬 Object Key 저장소를 하나의 Docker 볼륨에 둔다.

## 실행

1. 저장소 루트에서 `.env.example`을 `.env`로 복사하고 `HEATPIPE_API_TOKEN`에 충분히 긴 비밀 키를 설정한다. `.env`는 Git에 포함하지 않는다.
2. Docker가 작동하는 Linux/WSL 터미널에서 다음을 실행한다.

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

기본 바인딩은 `127.0.0.1:18001`이다. Compose는 `HEATPIPE_ENV=production`을 고정해 `/docs`, `/redoc`, `/openapi.json` 경로를 만들지 않는다(HTTP 404). `/health`와 `/version`은 계속 인증 없이 확인할 수 있다. OpenAPI 정적 사본은 `docs/openapi.json` 파일로 별도 전달한다. VTI 시험 서버에서는 HTTPS 프록시 뒤에 배치하고, 필요한 네트워크 정책에 맞게 `HEATPIPE_BIND_HOST`를 설정한다. Bearer 키는 VTI 백엔드와 서버 사이에서만 사용한다.

VTI가 시험용 Presigned URL의 **호스트명**을 별도 전달하면, 연동 시험 전 `.env`의 `HEATPIPE_ALLOWED_SOURCE_HOSTS`에 등록한다(쉼표로 복수 호스트 구분, 스킴·경로·포트·서명 쿼리는 넣지 않음). API와 워커가 같은 설정을 사용하므로 변경 후 `docker compose up -d --force-recreate api worker`로 적용한다. 시험 서버처럼 서로 다른 서버 간 파일 전송은 HTTPS를 사용한다. 최종 동일 서버 배포에서는 포털 백엔드→분석 API와 분석 워커→MinIO를 공유 Docker 내부망의 HTTP 서비스 주소로 연결한다. 후자의 **MinIO 서비스 호스트명만** `HEATPIPE_ALLOWED_INTERNAL_HTTP_HOSTS`에 등록한다. HTTP 허용 목록은 기본적으로 비어 있고, 외부 호스트는 등록하지 않는다. 실제 내부 서비스 이름·포트·네트워크 연결은 VTI Compose 구성과 함께 확인한다. Presigned URL의 서명 경로·쿼리를 변경하거나 리다이렉트하지 않아야 한다.

API와 분석 워커는 `heatpipe_data` 볼륨을 공유한다. `/data/jobs.sqlite3`에 작업 상태, `/data/objects`에 입력 및 결과 파일, `/data/work`에 처리 중 임시 파일이 있다. CPU 배포 갱신은 `docker compose up -d --build`를 사용한다. 볼륨을 지우는 명령은 사용하지 않는다.

VTI 연동 정책에 따라 `SUCCEEDED` 작업의 결과 JSON·후보 PNG는 완료 시각부터 최소 7일 보관한다. 현재 자동 삭제 기능은 없으므로 배포·갱신 시 `heatpipe_data` 볼륨을 유지하고 디스크 사용량을 점검한다. 시험 서버에서 7일치 결과의 저장 공간과 장애 시 복구 절차를 확인해야 한다. 7일 이후 삭제 시점은 별도 운영 정책으로 정한다.

DaonH&S GPU 시험 서버의 목표 배포는 [선택형 GPU 구성](GPU_ANALYSIS.md)의 `compose.gpu.yaml`을 함께 사용하는 방식이다. API는 CPU 이미지, 분석 워커만 GPU 이미지를 사용한다. 시험 서버에 실제 배포하기 전 GPU 모델·드라이버·Docker GPU 지원을 확인해야 한다.

## 로컬 컨테이너 검증 재현

이 절은 작은 합성 파일로 **HTTP → API → SQLite → 분석 워커 → 결과 JSON·PNG** 경로를 확인한다. 프로젝트의 기존 Python 환경에서 다음을 실행한다.

```powershell
& '..\heat\Scripts\python.exe' scripts/docker_smoke.py prepare
```

Docker가 실행되는 Linux/WSL 터미널에서 같은 저장소로 이동한 뒤 입력을 공유 볼륨에 넣는다.

```sh
docker compose exec -T api mkdir -p /data/objects/inputs
docker compose cp tmp/docker-smoke/docker-smoke.att api:/data/objects/inputs/docker-smoke.att
docker compose cp tmp/docker-smoke/docker-smoke.atg api:/data/objects/inputs/docker-smoke.atg
```

Windows 터미널에서 API 요청·상태 조회·결과·PNG를 검증한다. 스크립트는 `.env`의 키를 읽지만 출력하지 않는다.

```powershell
& '..\heat\Scripts\python.exe' scripts/docker_smoke.py verify
```

실제 ATT·ATG 전체 프레임은 이후 [GPU Docker API 검증](REAL_DATA_GPU_DOCKER_2026-11-02.md)에서 분석했다. 아래 최초 CPU Docker 검증은 합성 4프레임이다.

## 2026-11-02 검증 결과

| 항목 | 결과 |
|---|---|
| WSL Ubuntu Docker Compose 빌드·시작 | 통과, API healthy·분석 워커 실행 |
| 당시 개발 구성의 호스트 `127.0.0.1:18001` | `/health`, `/version`, `/openapi.json` 응답. 현재 운영 Compose에서는 `/openapi.json`을 비활성화함. |
| 인증된 INT-003 → INT-004 → INT-001 | `SUCCEEDED`, 4/4프레임, 진행률 100%, 후보 1건 |
| ROI ATT 온도 통계 | 최저 29.77℃, 평균 30.4℃, 최고 35.18℃ |
| 후보 PNG | 인증된 API에서 `image/png` 응답 |
| 재시작 후 SQLite 상태 | `SUCCEEDED`·100% 유지 |
| 임시 작업 폴더 | 해당 작업 폴더 제거 확인 |

VTI 자료의 제안 시험 서버는 Ubuntu VM 4 vCPU·8 GB RAM·32 GB 디스크이며, 하루 ATT+ATG 2세트와 공용 계정 3명을 가정한다. **이 사양에서 실제 770 MB급 입력을 Docker로 처리하는 성능·디스크 여유·동시 요청은 아직 검증하지 않았다.** 실제 서버 크기, 파일 보관 기간, 동시 작업 수, VTI 저장소 접근 방식과 HTTPS 연결을 Q26/연동 시험에서 확인해야 한다. 현재 분석 워커는 한 프로세스 운영을 전제로 한다.

VTI 슬라이드의 포털 전체 산출물(앱·DB·MinIO Compose, 초기 계정/데이터, 포털 UAT)은 DaonH&S 분석 서비스 컨테이너의 구현 범위와 구분한다. 연동 시험은 VTI가 실데이터 파일 참조와 API 호출을 제공하고, DaonH&S 서버에서 작업 완료·결과 조회를 함께 확인하는 순서로 진행한다. 현재 확정된 개발 범위는 [INT-001·003·004 범위 문서](INT_001_003_004_DEVELOPMENT_SCOPE.md)를 따른다.
