# Claude 검토 요청: 서버 이전 결과와 남은 코드 배포 준비

기준: 2026-11-19. 실제 적용과 로컬 준비를 구분한다. **추가 서버 변경 없이 검토만 요청한다.**

## 후속 상태 업데이트 — r3 빌드·격리 검증 완료

아래 초기 후보 준비 기록보다 이 업데이트가 우선한다. 운영 배포는 아직 하지 않았다.

- 포털 Compose의 미적용 /data/data bind 한 줄을 기존 minio-data 볼륨으로 복구했다.
  백업을 남겼고 병합 결과 heatpipe-portal_minio-data 확인. 포털 재배포는 하지 않았다.
- Docker mountpoint 실행/설정 문법 검사는 통과했으나 list-dependencies에서 data.mount가
  표시되지 않았다. 실제 기동 의존성과 재시작/재부팅 검증은 남아 있다.
- 기존 이미지의 의존성을 유지한 r3-20261119-build2 이미지 생성 완료.
  ID: sha256:5e007f46e1856316d7c8c45cc715693955b43505cb3dd34c41d93decb08c7948.
- 첫 빌드는 기반 이미지 /app/build의 구버전 산출물을 재사용해 실패했다. build2는
  새 빌드 폴더/force-reinstall로 수정했고 설치 패키지 26파일 SHA 및 의존성 동일 확인.
- /data/data/backups/r3-image-validation-yguRmU의 격리 복원본에서 기존 PNG 50개와
  합성 URL 분석 PNG 1개, SUCCEEDED 100, 같은 키 재요청, null path 제외, 원본 보존 확인.
  path 모드는 비활성 유지. 임시 컨테이너 삭제, 운영 서비스/태그/데이터 변경 없음.
- 운영 이미지는 여전히 기존 bc8e9b3395a9…다. 신규 커밋/태그 생성은 하지 않았다.
- 상세 절차와 검증 한계: R3_DEPLOYMENT_PREPARATION_20261119.md.

후속 롤백 실검증도 완료했다. 실제 pre-storage 구버전 이미지가 r3 생성 URL 작업
JOB-20261119-000028의 상태/결과/PNG 1개를 조회하고 동일 키 재전송에 같은 ID를 반환했다.
격리 복사본 작업 28건/최대 ID 유지, REAL_OLD_IMAGE_ROLLBACK_PASS, Exit=0.
검증 경로: /data/data/backups/r3-rollback-validation-cd4XoG. 운영은 변경하지 않았다.
Docker show의 After에 data.mount가 확인됐으나 Requires에는 없었다. ExecStartPre
마운트 검사는 유지돼 있으며 실제 재기동 검증은 여전히 미실행이다.
URL 모드 r3 배포는 VTI 포털 수정 완료를 기다리지 않고 중단 시간 합의 후 진행 가능하다.
배포 소스 커밋/버전 고정은 아직 미실행이며, 공유 원본 전환은 VTI 준비 후 별도 단계다.

## 1. 외부 계약과 담당 범위

- VTI에 Q15 v1.1 방안 A 명세·OpenAPI·예시를 메일/메시지로 전달했다.
- ATT·ATG만 일반 원본 공유로 전환하고, 나머지 포털 파일은 MinIO 유지.
- 다온 담당은 분석 API·워커. 포털·MinIO·포털 DB 전환은 VTI 담당이다.
- 신규 path 방식은 아직 서버에 제공하지 않는다. 현재 URL 계약은 유지 중이다.

## 2. 서버에서 실제 완료한 작업

### 실행 코드와 현재 서비스

- API·워커 모두 기존 이미지
  `sha256:bc8e9b3395a9e79100c3d3a3422d776eb67848a5bced06bdd4d12156b98c43dc`를 그대로 사용한다.
- 실행 중 models.py와 store.py의 LF 정규화 SHA-256은 git 7e6137의 같은 파일과 일치했다.
  revision 라벨이 없어 이미지 전체가 그 커밋이라고 단정하지는 않았다.
- **r3 코드는 서버 미배포**, path_field=False였고 이후에도 이미지 변경은 없었다.
- 이전 완료 후 사용자 ps: API healthy, worker 실행 중. 공개 /health도 ok·worker_running=true.
- 내부 API `http://daon-api:8000`, 공개 API `https://heatpipe.daonhns.com`,
  호스트 10.1.2.2:18001→컨테이너 8000 유지.

### 백업·복원

- 기존 이미지를 `heatpipe-service:pre-storage-20261119`로 보존하고 이미지 tar(172M),
  소스·설정 tar(44K)를 만들었다. `.env`를 포함한 백업은 서버 비공개 폴더에만 보관한다.
- 백업 위치: `/data/data/backups/analysis-pre-move-20261119-B5B0pB`.
- 활성 작업이 없을 때 API·워커를 정지해 WAL checkpoint/integrity 확인 후 데이터 백업.
- 격리된 복원본의 DB·작업 건수·전체 파일/메타데이터 비교 성공.
- network none, 외부 포트 미공개 복원 API를 띄워 기존 결과와 PNG 크기·SHA-256 확인.
- 원래 서비스를 다시 시작했다. 첫 시도는 볼륨 경로의 sudo 없는 사전 검사에서 멈췄고,
  수정 후 완료했다. 그 실패 시점에는 API·워커가 정지되지 않았음을 확인했다.

### 저장소와 기동 조건

- LXC 내부 systemd, /data는 호스트 제공 ext4 마운트이며 fstab/UUID를 추가하지 않았다.
- 분석 데이터를 `/data/data/analysis`로 복사하고 API·워커 모두 해당 경로를 /data로 bind.
- 원본 볼륨 `heatpipe-service_heatpipe_data`는 삭제하지 않았다.
- 이전 직전 최신 백업·환경·검증 기록:
  `/data/data/backups/analysis-move-20261119-omjGbm`.
- 기존 작업 27건, JOB-20261119-000027의 결과와 PNG 무결성을 확인했다.
- 컨테이너 df: 251G 디스크, 가용 약 238G.
- Docker drop-in에 RequiresMountsFor=/data와 mountpoint ExecStartPre를 추가하고
  daemon-reload만 했다. Docker 전체 재시작/서버 재부팅은 하지 않았다.
- 처음 install이 숫자 UID를 거부해 멈췄고, Python의 숫자 chown으로 10001:10001을
  지정했다. 현재 분석 디렉터리 모드는 0750이다.

### 현재 서버 운영 명령의 변경

서버 .env:

```text
COMPOSE_FILE=compose.yaml:compose.portal.yaml:compose.external-data.yaml
HEATPIPE_EXTERNAL_ANALYSIS_ROOT=/data/data/analysis
```

기본 docker compose는 분석 api/worker만 대상으로 한다. Caddy https는 중지 상태로
기본 파일 목록에서 제외했다. orphan 경고는 예상된 것이며 삭제하지 않았다.
명시적으로 이전의 세 파일(https 포함)만 지정하면 잘못된 bind 설정이 반영될 수
있으므로 사용하지 않는다. keep-current-data도 이제 옛 볼륨 복귀용이므로 다시 적용하지 않는다.

VTI 포털·MinIO·포털 DB의 설정/재배포는 이번 분석 이전에서 수행하지 않았다.
다만 Docker 공통 기동 조건은 위와 같이 변경했다. 포털 공개 HTTPS는 작업 후 200을 재확인했다.

## 3. 로컬 코드 준비

- r2: 명시적 path, 무결성 검증, 원본 ro, stale symlink 복구, API/워커 표식 확인.
- r3: INSERT/QUEUED UPDATE 두 곳에서 request_json 저장에 exclude_none=True 적용.
- 단위 테스트 87개 통과. r3 ZIP 압축 해제본에서도 87개 통과.
- 구버전 모델·create 메서드 snapshot으로 새 URL/object_key의 대기·완료 동일 키 재전송 확인.
- 실제 path 값과 기존 저장된 null 포함 기록은 자동으로 구버전 호환되지 않는다.
- 요청·응답·OpenAPI 외부 계약은 r3 저장 보완에서 변경하지 않았다. VTI 송부본도 유지했다.
- 커밋·푸시는 하지 않았다. 이전 미커밋 algorithms.py의 포맷 변경과 experiments는 별도다.

## 4. 배포 후보 준비

기준 커밋 7e6137의 소스를 바탕으로 검토 대상 API/워커/저장소 파일만 덮어쓴
후보를 만든다. 알고리즘 포맷 수정과 experiments는 포함하지 않는다.
파일별 SHA-256으로 후보를 식별하되, 이것이 신규 Git 커밋/릴리스 확정을 뜻하지는 않는다.
후보 자체의 테스트 결과와 파일 목록은 별도 CANDIDATE_MANIFEST/검증 기록을 따른다.
후보는 검토·빌드 준비용이며 서버에 자동 적용하는 설치 스크립트가 아니다.

## 5. 남은 작업

1. 검토 후보의 차이·테스트 확인 후 사용자 지시에 따라 커밋/배포 버전 고정.
2. 현재 의존성과 이미지 빌드 방식을 확인하고 r3 이미지 생성·로컬/격리 검증.
3. **추가 재기동·재배포는 VTI와 중단 시간대를 맞춘 뒤에만 실행.**
4. VTI가 일반 원본 게시를 준비하면 raw 권한·같은 폴더 내 임시 저장/rename,
   표식·읽기 전용 마운트·기존 원본 이전을 함께 확인하고 path 방식 활성화.
5. 신규 업로드→분석→상태→JSON/PNG, 재분석·취소·실패·재시도·원본 보존 통합 시험.
6. 실제 재부팅의 기동 순서와 호스트 차원의 별도 백업/스냅샷 범위는 담당자와 확인.

이번 이전 중 API·워커를 잠시 중단한 사실과 복구 결과는 확인했지만, 해당 시간에
VTI 요청 실패가 있었는지는 확인하지 못했다. 이후 서비스 영향 작업은 사전 협의한다.
이전 후 기존 결과 읽기 검증은 완료했으나, 신규 MinIO URL 원본을 내려받는 실파일
분석이 완료됐다고 확대 해석하지 않는다.

## 요청할 검토

- 실행 사실과 로컬 준비 상태가 혼동 없이 기록됐는지.
- 새 /data bind와 default COMPOSE_FILE, 옛 볼륨 보존 및 롤백 제약이 적절한지.
- 백업/복원과 기동 조건, 신규 작업 발생 후 복구에 누락이 있는지.
- r3 배포 전 추가로 준비할 항목이 있는지. 코드/운영 파일 변경이나 서버 작업은 하지 말 것.
