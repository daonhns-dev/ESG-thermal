# 256GB 스토리지 이전 및 방안 A 전환 준비

## 최신 상태: 분석 저장소 이전 완료 (2026-11-19)

사용자 서버 실행 결과 MIGRATION_SUCCESS를 확인했다. 아래의 미실행/대기 기록은
진행 경과이며 이 최신 상태가 우선한다. VTI 포털/MinIO 데이터는 이번에 이전하지 않았다.

- API·워커의 /data는 모두 호스트 `/data/data/analysis` bind로 전환했다.
- 기존 이미지 bc8e9b3395a9…를 그대로 사용한다. r3 코드 배포와 path 활성화는 미실행이다.
- 원본 named volume `heatpipe-service_heatpipe_data`는 삭제하지 않고 보존했다.
- 새 경로 복사 직전의 최신 오프라인 백업, SQLite 검사, 파일/메타데이터 대조 성공.
- 기존 작업 27건 유지. JOB-20261119-000027 결과와 PNG 크기·SHA-256 확인.
- API healthy, worker_running=true, queued_job_count=0 확인.
- 컨테이너 내부 df는 disk--1, 251G, 가용 약 238G로 확인됐다.
- MOVE_STATE: `/data/data/backups/analysis-move-20261119-omjGbm`.
  원래 .env/Compose와 이전 직전 데이터 백업·검증 결과가 보존돼 있다. 비밀값 포함
  가능성이 있는 백업은 외부에 공유하지 않는다.
- 서버 .env COMPOSE_FILE은 `compose.yaml:compose.portal.yaml:compose.external-data.yaml`,
  HEATPIPE_EXTERNAL_ANALYSIS_ROOT는 `/data/data/analysis`다. 기본 up은 분석 두
  서비스만 사용한다. Caddy https는 기본 목록에서 제외했고 기존 중지 컨테이너를 보존했다.
- 작업 후 공개 분석 API /health는 status=ok/worker_running=true, 공개 포털은
  인증서 검증을 포함해 HTTP 200을 재확인했다.

운영 주의: 앞으로 분석 스택은 위 서버 .env의 기본 Compose 목록 또는 같은 명시적
파일 목록을 사용한다. 이전 `compose.yaml + compose.https.yaml + compose.portal.yaml`
세 파일만 지정하면 준비 중이던 잘못된 기본 bind가 반영될 수 있다. keep-current-data는
옛 볼륨 고정용이므로 지금 다시 추가해 적용하지 않는다. 새 DB에 쓰기가 발생할 수 있어
이전 볼륨으로 단순 되돌리지 않고 정합성·신규 작업을 확인한 뒤 복구한다.

남은 작업: 검토 통과한 r3 코드의 커밋 기준 확정·새 코드 배포, VTI 준비 후 raw ro 연결과
path 통합 시험, 실제 재부팅 시 마운트 기동 검증. 이번 이전 후 새 URL 원본을 내려받는
신규 실파일 분석은 별도 확인이며, 기존 결과 조회만으로 그 시험을 완료 처리하지 않는다.

> 저장소 이전 사전 검사: 서버에서 migrate-analysis-storage.sh --preflight-only가
> 기존 이미지·소스/설정·데이터 백업 checksum, 빈 대상 폴더·권한·마운트 의존성,
> 기존 이미지/URL 모드의 병합 설정, 활성 작업=[]를 확인하고 PREFLIGHT_PASS로 끝났다.
> 실제 실행 명령을 안내했으며 MIGRATION_SUCCESS 결과는 아직 대기 중이다.

> 2026-11-19 분석 저장소 이전 준비:
> `/data/data/analysis`는 10001:10001/0750의 빈 폴더로 생성했다.
> Docker drop-in의 RequiresMountsFor=/data와 mountpoint ExecStartPre를 daemon-reload로
> 로드했고 ActiveState=active를 확인했다. Docker/컨테이너 재시작과 부팅 시험은
> 이 설정 작업에서 수행하지 않았다. install의 숫자 UID 조회 오류는 Python chown으로 처리했다.
> `scripts/migrate_analysis_storage.sh`는 기존 이미지를 유지하는 저장소 전용 이전안이다.
> 서버 preflight와 실제 실행은 아직 하지 않았다.

> 2026-11-19 데이터 백업·격리 복원 검증 완료:
> `/data/data/backups/analysis-pre-move-20261119-B5B0pB/analysis-data.tar.gz`.
> 활성 작업 0에서 API·워커를 정지했고, SQLite WAL checkpoint/integrity,
> 복원 작업 건수·전체 파일/메타데이터 비교를 통과했다. 격리 복원 API health와
> JOB-20261119-000027의 기존 결과 및 후보 PNG 크기·SHA-256도 통과했다.
> 원래 컨테이너 재시작 완료 Exit=0. 실제 운영 저장 경로 이전과 코드 배포는 미실행이다.
> 앞의 첫 시도 실패 기록은 이 성공 기록으로 후속 처리됐으며, 원래 서비스 health는 별도 재확인한다.

> 백업 실행 기록: 첫 데이터 백업 시도는 볼륨 경로의 일반 계정 접근 검사에서
> 종료 코드 1로 멈췄다. 사용자 확인 결과 API·워커 running=true,
> energy_access=denied, sudo_access=ok였다. 서비스 정지·데이터 백업 전 실패다.
> 검사 순서를 sudo 인증 후 sudo test로 수정하고 오류 위치 출력을 추가했다.
> 수정본 재실행 결과는 아직 대기 중이다. 기존 이미지·소스 백업은 보존돼 있다.

> 2026-11-18 VTI 회신 후 방안 A로 범위 변경: ATT·ATG만 일반 파일 공유로 전환하고
> 나머지는 MinIO 유지. 최신 계약은 Q15_SHARED_PATH_V1_1_DRAFT.md다.
> MinIO 전체 제거는 범위 밖이다. ATT·ATG 외의 기존 파일은 MinIO에 유지한다.
> 포털 경로는 /data/data/portal/{raw,db,minio}, 분석 경로는 /data/data/analysis를 제안한다.

기준: 2026-11-18. 이 문서는 이전 계획이며 서버 적용 완료 기록이 아니다.

## 2026-11-19 읽기 전용 현황 수신

| 항목 | 확인 결과 |
|---|---|
| /data | /dev/mapper/pve-vm--200002--disk--1, ext4, 251G 중 가용 239G |
| 루트 / | 32G 중 16G 사용, 가용 14G |
| fstab /data 항목 | findmnt --fstab --mountpoint /data 결과 없음 |
| lsblk -f | nvme0n1과 파티션만 보이고 /data의 mapper/UUID는 보이지 않음 |
| DockerRootDir | /var/lib/docker |
| 서버 Compose | v5.7.0 (로컬 config 검증 환경 v5.3.1과 구분) |
| 분석 볼륨 | du 약 50M, docker system df 논리 크기 약 50.96MB |
| PostgreSQL | du 약 69M, docker system df 논리 크기 약 71.65MB |
| MinIO | du 약 8.5G, docker system df 논리 크기 약 9.015GB |
| 기타 | 인증서 36K, acme-webroot 4K, pipe-network 116K |

공간 판단: 현재 측정 데이터는 총 약 8.6GiB다. 외부 디스크 운영 사본 + 백업 +
ATT·ATG 내보내기(최대 현재 MinIO 데이터량 상당) 기준 대략 26GiB 수준에 추가
작업 공간·증가분을 잡을 수 있어, 현재 239GiB 가용 공간에서는 이전 준비가 가능하다.
이는 live 상태의 du 추정치이며 실제 파일별 합계·중단 후 측정으로 다시 계산한다.
루트 디스크에 대형 백업을 추가 생성하는 계획은 피한다. 이미지/로그 증가분도 남겨 둔다.

기동 설정 주의: fstab에 항목이 없다는 사실만으로 UUID 항목을 추가하지 않는다.
Proxmox/LXC 호스트가 외부에서 mountpoint를 제공하는 구성일 가능성이 있으므로,
systemd-detect-virt와 data.mount 상태, 서버 담당자의 호스트 설정을 확인한다.
그 결과 전에는 아래 docker.service drop-in 예시를 적용하거나 /data를 해제하지 않는다.
lsblk에서 디스크가 안 보이는 상태로 UUID를 추정하지 않는다.

추가 확인 중: 실제 가상화·PID1·data.mount/docker.service, API/워커 UID/GID,
이사님 수정까지 포함한 양쪽 Compose 병합 마운트와 파일 권한.
아직 서버 설정 변경·서비스 중지·데이터 복사는 하지 않았다.

### 실행 이미지 및 롤백 기준 확인

서버 API·워커와 heatpipe-service:local은 모두
`sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc`다.
revision 라벨은 없다. 실행 중 models.py와 store.py를 LF 정규화해 계산한 해시는
git 7e6137의 같은 파일과 일치한다.

- models.py: 2b29af7a35d60a60a542ca1784b50df2d4b69fd07afcc0658749ea679a2b739b
- store.py: 840505cce94bd5daa6a182a2c2de2b38bf72971edc2d2278c56067f2b636dad8

path_field=False, 활성 작업=[] 확인. 이 두 파일의 롤백 모델/재전송 기준은 맞지만
이미지 전체가 같은 커밋으로 빌드됐다고 단정하지 않는다. 전체 이미지를 별도로 보존한다.
최신 df는 루트 가용 14G, /data 가용 239G다. 이전 17G는 과거 기록이다.

`scripts/prepare_analysis_storage_backup.sh`는 서비스 중단 없이 기존 이미지를
pre-storage-20261119 태그와 외부 스토리지 tar로 보존하고 소스·설정을 함께 묶는
준비 스크립트다. .env가 포함되므로 umask 077로 비공개 디렉터리/파일을 만든다.
이 파일은 서버 로컬 복구용이며 AI 검토 ZIP이나 VTI에 전달하지 않는다.
이 단계는 DB/결과 볼륨의 일관된 백업을 대신하지 않는다. 데이터 백업은 writer 정지 후 별도 실시한다.

사용자 실행 결과: 이미지/소스 보존 완료, API·워커 미중단.
백업 디렉터리는 `/data/data/backups/analysis-pre-move-20261119-B5B0pB`.
analysis-image.tar.gz 약 172M, source-and-settings.tar.gz 약 44K.
prepare 스크립트의 gzip/tar 및 checksum 작성까지 성공했다. 데이터 볼륨 백업은 아직 별도다.

다음 준비 스크립트 `scripts/backup_verify_analysis_data.sh`는 활성 작업이 없을 때
API를 먼저 정지하고 작업 유입 race를 다시 확인한 뒤 워커를 정지한다. 현재 볼륨의
SQLite WAL checkpoint/integrity 확인 → tar 백업 → 격리 폴더 복원 → 파일/메타데이터
checksum 비교 → 복원 DB 무결성·작업 건수 확인을 수행한다. 원래 컨테이너를 다시
시작한 뒤 network none/미공개 포트의 별도 복원 API로 health·기존 결과·PNG 샘플을
확인한다. 포털/MinIO는 조작하지 않는다. EXIT trap은 정지했던 원래 컨테이너를
재시작하고 이 스크립트가 만든 임시 복원 컨테이너만 정리한다. 아직 서버에서 미실행이다.

### 추가 현황 수신: LXC와 실행 권한

- `systemd-detect-virt=lxc`, PID 1은 systemd다. `/data`의 data.mount는 active이고
  SourcePath=/proc/self/mountinfo, FragmentPath는 없다. 호스트가 제공한 마운트로
  취급하며 게스트 fstab/UUID를 임의로 추가하거나 /data를 umount하지 않는다.
- docker.service는 /usr/lib/systemd/system/docker.service이고
  RequiresMountsFor는 비어 있다. 드롭인은 mount 의존성과 mountpoint 사전 검사를
  사용하는 안을 준비하되, LXC 호스트의 mountpoint 설정·부팅 동작은 서버 담당자와
  별도 확인한다. 서비스 변경/재시작은 아직 미실행이다.
- 포털 API의 실행 UID/GID는 0:0, 워커는 10001:10001이다. 현재 분석 볼륨은
  10001:10001/0755, PostgreSQL은 70:70/0700이다. 이전 시 소유권/모드를 유지한다.
- /data는 0:0/0755, /data/data는 1000:1000/0755다. 새 analysis 폴더에 워커가
  쓸 수 있도록 최종 권한을 정한다. raw는 포털이 게시한 파일을 UID 10001이 읽을 수
  있도록 디렉터리 탐색권한·파일 읽기권한을 보장한다. ro 마운트는 읽기 권한을 부여하지 않는다.
- 원본 디렉터리는 공유 그룹 10001과 setgid 등으로 구성하는 안을 VTI와 맞춘다.
  예: raw 및 하위 디렉터리 0:10001/2750, 원본 파일 0:10001/0640.
  단 포털의 임시 파일 생성/rename/umask가 이를 보존하는지 실파일로 시험한 뒤 확정한다.
  root 실행이라는 이유만으로 기존 파일이 자동으로 워커에 읽힌다고 가정하지 않는다.
- UID/GID는 LXC 게스트 안에서 측정한 숫자다. 호스트의 LXC ID 매핑을 추정해
  호스트에서 chown하지 않는다. PostgreSQL까지 일괄 10001로 바꾸거나 chmod 777하지 않는다.

남은 현황 확인: 양쪽 Compose 병합 마운트·네트워크와 실제 게시 원본 파일 권한.

### 서버 Compose 병합 결과 수신 (2026-11-19)

| 서비스 | 현재 파일을 병합한 /data 대상 | 앞선 실행 컨테이너 inspect |
|---|---|---|
| 분석 api | bind /data/data | 기존 heatpipe-service_heatpipe_data |
| 분석 worker | named volume heatpipe_data | 기존 heatpipe-service_heatpipe_data |
| 포털 minio | bind /data/data | 기존 heatpipe-portal_minio-data |

**현재 파일 그대로 up/recreate하면 안 된다.** api와 worker가 서로 다른 DB를 보게
되고, api와 minio가 같은 호스트 폴더를 쓰게 된다. 현황 조회는 파일 병합 결과와
실행 상태를 구분하며, 이 위험이 이미 적용됐다고 단정하지 않는다.

준비한 고정 오버레이:

- 분석 `compose.keep-current-data.yaml`: api/worker 모두 실제 기존 볼륨
  heatpipe-service_heatpipe_data를 external name으로 명시한다. 코드만 바꾸는
  1단계에서 기존 세 Compose 뒤에 추가하고, effective config와 실제 volume inspect를
  대조한 뒤 api worker만 지정해 작업한다. 기본 3파일만으로 up하지 않는다.
- 포털 `deploy/portal/compose.keep-current-minio.yaml`: VTI와 포털을 작업할 경우
  기존 실제 heatpipe-portal_minio-data를 external로 고정할 수 있는 준비 파일이다.
  아직 서버로 보내거나 적용하지 않았다. 포털 작업 전 VTI와 함께 config를 검증한다.
- external volume은 미존재 시 새 빈 볼륨을 생성하지 않고 오류로 멈춘다.
- 분석 2단계의 데이터 이전 완료 후에는 keep-current를 빼고 external-data로 전환한다.
  shared-input은 VTI의 일반 원본 준비가 끝난 3단계에서만 추가한다.
- 포털의 postgres/letsencrypt/acme-webroot는 파일 기준으로도 아직 기존 볼륨이다.
  portal-api에는 현재 pipe-network 읽기 전용 bind만 있고, raw bind는 아직 없다.
- 기존 API 10.1.2.2:18001→8000, daon-api 별칭과 default/heatpipe-shared,
  포털 80/443와 portal-minio 별칭은 병합 결과에 유지돼 있다.

실제 마운트·게시 파일 권한은 각 적용 직전 다시 확인한다. 현재 준비 오버레이를
기존 unsafe Compose 변경의 자동 영구 수정으로 간주하지 않는다. 적용 명령과
운영자가 쓰는 Compose 파일 목록까지 함께 관리해야 한다.

### 기존 데이터 고정 오버레이 서버 검증 완료

2026-11-19 사용자가 새 오버레이 두 파일을 서버에 전송했다. 기존 Compose는
덮어쓰지 않았다. 서버 Compose v5.7.0에서 양쪽 config --quiet를 통과했고,
병합 JSON의 /data 대상과 external volume 이름을 assert로 확인했다.

```text
PASS: api + worker -> heatpipe-service_heatpipe_data
PASS: minio -> heatpipe-portal_minio-data
```

이 결과는 실행 중 컨테이너의 변경을 뜻하지 않는다. up/restart/recreate나 데이터
복사는 하지 않았다. 1단계 배포 명령에는 분석 keep-current-data 오버레이가 반드시
들어가야 한다. 기본 세 파일만으로 재생성하면 앞서 확인한 잘못된 bind가 반영된다.
코드 배포본 확정 전 롤백 호환 보완 여부와 사용자 지시의 커밋 기준이 남아 있다.

2026-11-19 후속 결정: 사용자가 exclude_none 저장 보완 후 재검토를 선택했다.
로컬에서 신규/QUEUED 갱신 직렬화 두 곳을 수정했고, 단위 테스트 87개 통과.
실제 path와 기존 완료 기록은 자동 변환하지 않으므로 롤백 제한은 남는다.
서버 반영·커밋은 미실행이며 r3 재검토 및 코드 고정이 다음 관문이다.

## 목표와 유지할 동작

- ATT·ATG는 외부 스토리지에 일반 파일로 한 번 보관하고 워커가 직접 읽는다.
- 포털 업로드 → 분석 요청 → 상태 조회 → 결과 JSON·PNG 조회 흐름을 유지한다.
- 포털 다운로드, 원본 재사용 재분석, 취소, 재시도, 삭제·보관 정책도 기존 기능을 유지한다.
- 포털과 분석 서비스의 모든 영구 저장 마운트를 조사해 외부 스토리지로 옮긴다.
  컨테이너 실행 파일이나 시스템 소켓은 데이터 볼륨과 구분한다.

## 먼저 확인할 서버 상태

현재 서버의 분석 데이터는 `heatpipe-service_heatpipe_data`, 포털 MinIO는
`heatpipe-portal_minio-data`라는 서로 다른 볼륨에 있다. `/data/data`는 비어 있음을
확인했다. MinIO의 `.att`·`.atg`는 일반 파일이 아닌 디렉터리다.
256GB 스토리지는 `/data`에 ext4로 마운트되어 있음을 확인했다.
장치는 `/dev/mapper/pve-vm--200002--disk--1`, 표시 용량 251G, 가용 239G다.
기존 루트 디스크는 32G 중 14G 사용, 가용 17G다. 모든 실행 컨테이너의
기존 named volume 경로는 여전히 `/var/lib/docker/volumes/.../_data`다.
이사님이 수정한 포털 및 분석 Compose 전체 내용과 현재 유효한 실행 옵션도 확인한다.

서버 inspect로 확인한 이전 대상:

| 컨테이너/볼륨 | 현재 컨테이너 경로 | 처리 |
|---|---|---|
| API·워커 / heatpipe-service_heatpipe_data | /data | 공통 분석 데이터로 이전 |
| PostgreSQL / heatpipe-portal_pg-data | /var/lib/postgresql/data | 정지·일관된 백업 후 이전 |
| MinIO / heatpipe-portal_minio-data | /data | 저장 방식 전환 전까지 보존; 중간 이전 시 전용 폴더 사용 |
| frontend / heatpipe-portal_letsencrypt | /etc/letsencrypt (ro) | 인증서 볼륨도 외부 경로로 이전 대상 |
| frontend / heatpipe-portal_acme-webroot | /var/www/acme (ro) | 인증 검증 파일 볼륨도 이전 대상 |
| portal-api / /home/energy/heatpipe-portal/data/pipe-network | /data/pipe-network (ro) | 이미 bind mount지만 루트 디스크에 있으므로 외부 경로로 이전 대상 |
| 중지된 Caddy / heatpipe-service_caddy_config, heatpipe-service_caddy_data | /config, /data | 잔존 데이터 보관 여부 확인; 이전 때문에 서비스를 시작하지 않음 |

frontend의 nginx 구성 bind mount들은 서비스 설정 파일이며 별도 백업한다.
스토리지 이전을 위해 그 경로까지 바꿀 필요는 없다. 사용자가 설정 파일의
외부 보관까지 원하는 경우 별도 경로와 Compose 참조를 함께 변경한다.

## 디렉터리 배치안

사용자·개발 이사님 확인에 따라 `/data/data` 아래에 용도별 폴더를 만든다.
`/data`가 외부 디스크 마운트 지점이며, `/data/data`는 그 하위 디렉터리다.

| 서버 경로 예시 | 사용 서비스 | 목적 |
|---|---|---|
| `/data/data/analysis` | 분석 API·워커 | 기존 /data의 SQLite·결과·임시 작업 |
| `/data/data/portal/raw` | 포털 파일 저장 담당, API·워커(읽기 전용) | 원본 ATT·ATG 일반 파일과 준비 표식 |
| `/data/data/portal/db` | 포털 PostgreSQL | 기존 DB 데이터 |
| `/data/data/portal/minio` | MinIO | ATT·ATG 외의 파일 유지. 이전 원본도 검증 완료까지 보존 |
| `/data/data/letsencrypt` | 포털 frontend | 기존 인증서 볼륨 |
| `/data/data/acme-webroot` | 포털 frontend | 기존 인증 검증 파일 볼륨 |
| `/data/data/pipe-network` | 포털 API | 기존 관로 데이터 |

여러 서비스의 서로 다른 저장소를 같은 호스트 폴더에 덮어 연결하지 않는다.
VTI는 ATT·ATG 저장 어댑터를 일반 파일로 전환하고 기존 원본 조회·재분석 흐름을
유지한다. 결과 이미지·첨부 등은 MinIO에 유지한다. 이 저장소에는 VTI 포털 소스가 없다.

서버 확인: rsync는 `/usr/bin/rsync`에 설치돼 있다. 분석 실행 Compose는
`/home/energy/heat-pipe-service/{compose.yaml,compose.https.yaml,compose.portal.yaml}`,
포털 실행 Compose는 `/home/energy/heatpipe-portal/{docker-compose.prod.yml,docker-compose.daon-net.yml}`다.
현재 파일 설정과 실행 중 마운트가 다를 수 있으므로 이전 전 병합 설정을 확인한다.

## 우리 측 준비된 파일

- `compose.external-data.yaml`: `HEATPIPE_EXTERNAL_ANALYSIS_ROOT`를 API·워커의
  /data에 연결. 기존 내부 경로와 포트·네트워크는 유지한다. 미리 데이터 이전이 필요하다.
- `compose.shared-input.yaml`: `HEATPIPE_SHARED_INPUT_HOST_ROOT`를 API·워커의
  /shared-input에 읽기 전용으로 연결한다. API도 저장소 준비 표식을 확인한다.
- `storage/shared_inputs.py`, `jobs/worker.py`: 공유 원본 검증 후 링크로 읽으며
  다운로드·입력 복사를 생략한다. 임시 작업 폴더 정리로 원본을 지우지 않는다.
- 두 경로는 서로 겹치지 않게 설정한다. 원본 경로는 MinIO backing store가 아니어야 한다.

## VTI와 맞출 사항

1. MinIO를 사용 중인 업로드·원본 조회·결과 이미지·첨부·삭제 기능 전체 목록.
2. 원본은 임시 업로드 파일로 작성한 뒤 완료 시 원자적으로 공개하고 분석 요청한다.
   분석·재시도 중 변경 또는 삭제되지 않도록 한다.
3. 공유 경로의 파일 배치와 INT-003의 att.path/atg.path 상대 경로를 일치시킨다.
   URL/object_key는 예비용 기존 fetch 방식이며 공유 path로 해석하지 않는다.
4. 업로드 완료·크기·SHA-256 검증, 재전송 중복 방지 및 재분석 동작 유지.
5. 기존 포털 DB 참조를 유지/변환하고 MinIO의 기존 객체를 일반 파일로 내보낸다.
   이는 기존 데이터의 일회성 이전이다. 객체 폴더를 복사하는 것은 일반 파일 변환이 아니다.
6. 결과 PNG 등 나머지 사용처는 MinIO에 유지한다. MinIO 전체 제거는 별도 범위다.

## 전환 필수 체크리스트: 디스크와 Docker 시작 순서

Claude r2 재검토 후 추가한 운영 조건이다. **문서만 준비했으며 서버 systemd는 미변경이다.**

- [ ] 실제 Docker가 systemd의 docker.service로 관리되는지 확인한다. `/data`가
  재부팅 시 등록된 디스크(UUID)의 ext4로 마운트되도록 fstab/data.mount 구성을 확인한다.
- [ ] Docker 시작에 `/data` 마운트 의존성을 둔다. 공유 원본 표식만으로는 분석
  `/data`의 빈 SQLite 생성까지 방지하지 못한다. 아래는 검토할 drop-in 예시이며
  서버 담당자가 서비스/바이너리 경로를 확인한 뒤 중단 시간에 적용한다.

```ini
# /etc/systemd/system/docker.service.d/heatpipe-storage.conf
[Unit]
RequiresMountsFor=/data

[Service]
ExecStartPre=/usr/bin/mountpoint -q /data
```

`RequiresMountsFor`는 관련 mount unit에 Requires/After 의존성을 추가한다.
`ExecStartPre` 예시는 mount unit 등록 누락 시에도 /data가 단순 폴더인 상태의
기동을 막기 위한 추가 확인이다. 등록된 디스크가 맞는지는 별도 UUID/source 확인이
필요하다. 공식 근거: https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#RequiresMountsFor=

- [ ] Docker 재시작은 포털 전체에 영향을 줄 수 있으므로 포털 중단 시간대에만 한다.
  현재 동작 중인 서버에서 문서를 보고 즉시 restart/umount를 실행하지 않는다.
- [ ] 모든 관련 서비스를 정지한 오프라인 점검에서, 디스크가 마운트되지 않은 루트
  파일시스템의 `/data/data/analysis`에 폴더/새 jobs.sqlite3가 생겨 있지 않은지 확인한다.
  발견 시 자동 삭제하지 말고 기존 스토리지 데이터와 비교해 격리·복구한다.
- [ ] 디스크 마운트 → 실제 source/용량 확인 → 기존 데이터 이전 확인 → 원본 표식
  확인 → Docker/컨테이너 시작 순서로 진행한다. 빈 /data/data를 서비스가 사용하게 하지 않는다.
- [ ] 디스크보다 컨테이너가 먼저 시작됐다면 디스크를 나중에 마운트한 것만으로
  해결됐다고 보지 않는다. 신규 쓰기를 중단하고 잘못 열린 DB 여부를 확인한 뒤,
  올바른 마운트 상태에서 대상 컨테이너를 재시작/재생성하고 내부 df·기존 작업을 검증한다.
- [ ] 통합 시험에서 디스크 미준비 시 서비스가 잘못된 DB로 기동하지 않는지,
  정상 부팅 후 기존 작업과 포털 파일이 유지되는지 확인한다. 이는 실제 서버에서 미검증이다.

## 스토리지 일시 장애 후 재요청

API는 원본 표식 미준비 시 path 요청을 503으로 거부하며 이때 작업과 키를 기록하지
않으므로 같은 Idempotency-Key로 재접수할 수 있다.
접수 후 워커 처리 전에 저장소를 잃으면 SHARED_INPUT_NOT_CONFIGURED로 FAILED가
될 수 있다. 이 오류는 자동 재시도하지 않는다. 디스크·표식·마운트를 복구한 뒤
실패 job_id를 확인하고 **새 Idempotency-Key로 새 분석 요청**을 발급한다.
같은 키로 재요청하면 기존 FAILED 작업이 반환된다. INPUT_FILE_NOT_FOUND와
FILE_INTEGRITY_ERROR도 원인 해결 뒤 새 키가 필요하다. 실패 작업과 원본은 보존한다.

호스트 시작 순서 설정은 실행 중 디스크 분리까지 자동 복구하는 장치가 아니다.
운영 중 스토리지 장애는 신규 접수 중단·기존 작업 확인·복구 후 재요청 절차를 따른다.

## 실제 이전 순서

### 단계 분리 및 선행 결정 (2026-11-19 보완)

한 번에 코드·DB·원본 저장 방식까지 전환하지 않는다. 각 단계의 검증 결과를
기록하고, 다음 단계 전에 복구 가능한 백업/코드 식별자를 확인한다.

1. **현황·코드 고정 준비:** /data source/UUID/파일시스템/여유, fstab의 UUID·nofail,
   DockerRootDir와 볼륨 실제 크기, 서버 Compose 버전, 양쪽 병합 마운트 설정,
   포털·워커 실행 UID/GID와 파일 권한을 읽기 전용으로 확인한다. 전체 Compose에는
   비밀정보가 포함될 수 있으므로 공유할 출력은 volumes/user/networks 등으로 한정한다.
2. **롤백 호환 정책 확정:** exclude_none 저장 보완을 로컬 구현했고 r3 재검토 대기다.
   새 URL/object_key 기록은 불필요한 path:null을 저장하지 않는다. 기존 기록과 실제
   path 작업을 자동 호환시키지는 않는다. 구버전 파싱·재전송 회귀는 통과했으나
   실제 서버 이미지의 격리 복원 시험과 대기열 정리 절차는 별도로 유지한다.
3. **배포 코드 고정:** 이전 미커밋 작업을 임의로 섞지 않는다. 검토 대상과 이전 변경을
   확인한 뒤 사용자 지시에 따라 커밋/태그로 고정한다. 현 상태는 미커밋이며 아직
   배포본을 확정하지 않았다. 송부한 API 명세 ZIP과 서버 배포본은 구분한다.
4. **1단계 배포:** v1.1 코드만 배포하고 기존 named volume·URL 경로를 유지한다.
   shared-input/external-data 오버레이는 사용하지 않는다. URL 접수·상태·결과·PNG,
   기존 작업 조회·동일 키 재전송을 확인한다. VTI 포털 수정 완료와는 독립적이나
   서버 작업 시간과 진행 중 분석은 반드시 확인한다.
5. **2단계 이전:** /data 마운트 기동 조건을 먼저 검토/적용하고 분석 /data를 이전한다.
   external-data 오버레이만 추가하고 URL 흐름을 재검증한다. systemd daemon-reload나
   서비스 재시작과 부팅 시험은 구분한다. 실제 재부팅이 항상 필요한 것은 아니지만
   부팅 순서 보장은 협의한 중단 시간의 재부팅 시험으로 확인해야 한다.

현 진행 순서 보완: r3 코드 재검토/커밋 고정은 별도이므로, 검증된 기존 이미지를
그대로 유지한 저장소 이전을 먼저 할 수 있다. migrate_analysis_storage.sh는 코드
재빌드 없이 기존 이미지 ID를 확인하고, 최신 오프라인 사본·checksum을 만든 뒤
분석 데이터만 이전한다. 이 작업은 r3 배포나 path 활성화 완료로 표시하지 않는다.

이전 스크립트의 정상 경로는 `.env`의 COMPOSE_FILE을
`compose.yaml:compose.portal.yaml:compose.external-data.yaml`로 설정한다. 중지된
Caddy https 서비스가 평소 up 명령으로 시작되지 않게 default 파일 목록에서 제외한다.
기존 포트·네트워크·키는 유지한다. 원래 .env와 Compose는 비공개 MOVE_STATE 폴더에
보존한다. 명시적 -f는 COMPOSE_FILE을 무시하므로 이전 후 잘못된 기본 세 파일만
사용하지 않도록 운영 명령도 바꾼다.

컨테이너 교체 전 실패하면 원래 컨테이너/환경 복구를 시도한다. 교체 시도 이후에는
새 DB에 요청이 들어올 수 있어 자동으로 옛 볼륨으로 되돌리지 않고 중단 상황을
보고한다. source volume과 복사 대상, 최신 백업은 모두 보존해 후속 정합성을 판단한다.
6. **3단계 전환:** VTI의 일반 원본 게시·기존 원본 export·DB 위치 변경이 준비된 뒤
   원본 표식을 생성하고 shared-input 오버레이를 추가한다. 그때 path 통합 시험을 한다.

현재 named volume은 루트 디스크에 있으므로 외부 분석 bind로 전환하기 전부터
새 분석 DB 분기 위험이 이미 발생했다고 단정하지 않는다. 새 bind 전환 전에
기동 조건을 확보해야 한다. Docker/systemd 변경은 포털 전체 영향 범위를 확인한다.

### 공간과 백업·복원 검증

- `docker info`의 DockerRootDir와 inspect의 Source를 기준으로 실제 크기를 측정한다.
  명령 실행 중 데이터가 변하므로 준비 단계 측정은 추정이고 중단 후 다시 확인한다.
- 필요한 공간은 외부 디스크의 운영 사본 + 별도 백업 + MinIO에서 내보낼 ATT·ATG
  일반 파일 + 이전 중 임시/증가분의 합으로 계산한다. 단순 2배는 점검 기준일 뿐
  충분성 보장이 아니다. 같은 디스크 안의 백업은 디스크 장애에 대한 백업도 아니다.
- SQLite: API·워커 등 모든 writer 정지 후 WAL 체크포인트 성공 여부를 확인하고
  복사하거나, 일관된 DB 백업을 사용한다. raw 복사 시 -wal/-shm 누락 없이 세트로
  보존하며 live DB 파일 하나만 복사하지 않는다. 복원본 integrity_check와 기존
  작업 건수·완료 결과 조회를 확인한다.
- PostgreSQL: VTI와 버전·정지/덤프·복원 방식 및 소유권을 맞춘다. 실행 중 데이터
  디렉터리 단순 복사는 사용하지 않는다.
- **MinIO 저장소 디스크 이전:** MinIO를 완전히 정지하고 전체 backend 데이터를
  동일한 구성으로 옮기는 작업이다. 일반 ATT 파일 추출과 구분한다. VTI가 이전·
  권한·검증 절차를 확인하고 재기동 후 객체 조회를 시험한다.
- **ATT·ATG 일반 파일 전환:** MinIO가 정상 제공하는 S3 API로 export/mirror한 뒤
  DB의 크기·SHA-256과 대조한다. backend 디렉터리 단순 복사는 대체 수단이 아니다.
- 백업은 운영과 분리한 복원본으로 실제로 열어 본다. 복원 검증용 서비스가 운영
  포트·네트워크·작업 큐·실제 외부 요청에 연결되지 않도록 격리한다. 복원 서비스
  기동, DB 무결성·작업 이력, 결과/객체 샘플 조회까지 확인해야 원복 수단으로 인정한다.

### 단계별 실행 체크리스트

- [ ] 읽기 전용 현황 수집과 공간·권한 검토 완료.
- [x] exclude_none 적용 선택 및 로컬 회귀 검증 완료(87개). 서버 미적용.
- [ ] r3 재검토 및 기존 기록의 롤백 처리·복원 시험 완료.
- [ ] 사용자가 지시한 커밋/태그와 배포 파일·이미지 식별자 확정.
- [ ] 백업뿐 아니라 격리 복원 시험 완료.
- [ ] 1단계 URL 호환 배포 검증 완료.
- [ ] 마운트 기동 조건·분석 데이터 이전 및 2단계 URL 검증 완료.
- [ ] VTI 준비 완료와 중단 시간 확인 후 3단계 path 통합 시험.

아래 번호 목록은 실제 데이터를 옮기는 각 작업 시간에 적용할 공통 절차다.

1. 마운트 목록과 디스크 확인, 기존 경로·데이터 양·권한·UID/GID·실행 Compose 기록.
2. 변경된 Compose 백업, 서비스와 파일 업로드를 중지할 작업 시간 확보.
3. 신규 업로드·분석 접수 중단, 활성 작업 종료 확인 후 관련 쓰기 서비스 정지.
4. SQLite와 PostgreSQL의 일관된 백업 확보. DB는 실행 중 파일을 단순 복사하지 않는다.
5. 분석 /data와 포털 영구 데이터를 각각 해당 외부 폴더로 이전하고 권한 유지.
   PostgreSQL은 VTI의 동일 버전/백업·복구 절차를 따른다.
6. MinIO의 ATT·ATG 원본을 정상 S3 인터페이스로 일반 파일로 내보내 크기·해시·건수 대조.
   나머지 파일은 MinIO 전용 폴더로 이전해 기존 객체 API 조회를 유지한다.
7. VTI 저장 어댑터와 검증된 공유 원본 읽기 워커를 배포하고 Compose 재생성.
   기존 볼륨과 백업은 보존한다. `down -v`를 사용하지 않는다.
8. 기존 작업 조회, 신규 ATT·ATG 한 쌍 업로드, 분석·JSON·PNG·다운로드·재분석·
   실패·취소·재시도·원본 보존·삭제 정책 확인. 워커 fetch 미호출과 중복 원본 부재 확인.
9. 재기동과 외부 디스크 부재 시 동작 점검. 외부 디스크가 빠진 채 루트 디스크에
   데이터를 쓰지 않도록 호스트의 마운트/서비스 기동 의존성을 설정한다.
10. MinIO는 나머지 파일용으로 계속 운영한다. 기존 ATT·ATG 객체의 삭제는 이전·조회·
    재분석 검증과 양사 확인 후 별도 결정한다.

실패 시 신규 쓰기를 중단하고 이전 데이터와 새 데이터 차이를 확인한 뒤 복귀한다.
설정만 되돌리면 전환 후 생성한 작업이 사라져 보일 수 있으므로 DB·파일 일관성을 함께 복구한다.

## VTI 전달 문안 (방안 A)

동일 서버의 256GB 스토리지를 공통 저장소로 사용하도록 변경하려고 합니다.
ATT·ATG는 일반 파일로 한 번 저장하고 분석 워커가 공유 경로에서 직접 읽어,
상시 다운로드와 임시 원본 복사를 생략할 예정입니다. 사용자 업로드·조회·분석 및
기존 데이터 조회 흐름은 유지해야 합니다. ATT·ATG만 일반 파일 공유로 전환하며,
나머지 파일은 MinIO에 유지합니다. 새 상대 경로 필드와 배치는 Q15 v1.1 검토본을
기준으로 맞추고, 중단 시간대와 기존 데이터 이전·복구 절차를 함께 확정하겠습니다.
