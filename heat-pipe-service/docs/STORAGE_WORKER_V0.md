# 저장소와 분석 워커 v0

상태: DaonH&S 단일 서버 기술실증용 구현안.

## VTI와 파일 전달 방향

견적서와 현재 연동 범위의 기본 흐름은 **VTI가 ATT·ATG를 MinIO에 업로드 → VTI가 INT-003으로 파일 참조정보와 회차 메타데이터 전달 → DaonH&S가 접수 확인(HTTP 202)과 `job_id` 반환**이다. DaonH&S가 VTI의 원본 ATT·ATG를 MinIO에 업로드하는 흐름은 아니다.

분석 워커에는 원본 파일의 바이트를 읽을 경로가 필요하다. VTI가 **Presigned GET URL**을 보내면 DaonH&S는 MinIO 계정 없이 허용된 HTTPS 호스트에서 다운로드할 수 있다. **Object Key만** 보내면 공유 저장소 접근이나 MinIO 읽기 권한이 별도로 필요하다. 현재 구현의 `MinioStorage`는 이 경우를 위한 선택지이며 확정 연동 방식은 아니다.

결과 제공 기본안은 DaonH&S가 설정한 로컬 저장소에 JSON·PNG를 보관하고, VTI 백엔드가 DaonH&S API로 읽는 것이다. INT-001은 결과 JSON을 응답 본문으로 반환하고 후보 `result_image.path`에 상대 API 경로를 넣는다. VTI 백엔드는 DaonH&S API의 기준 주소와 이 경로를 합쳐 Bearer 키로 PNG를 조회하고 화면에 제공한다. 키는 VTI 프런트엔드에 전달하지 않는다. 따라서 VTI가 결과용 MinIO 버킷에 접근하거나 DaonH&S가 VTI MinIO에 결과를 업로드할 필요가 없다.

## 로컬 저장소 실행

기본 설정은 `HEATPIPE_STORAGE=local`, `HEATPIPE_STORAGE_ROOT=data/objects`, `HEATPIPE_WORK_ROOT=data/work`, `HEATPIPE_JOB_DB_PATH=data/jobs.sqlite3`이다. API와 분석 워커는 같은 작업 DB 경로를 사용해야 한다. 두 프로세스를 `heat-pipe-service` 폴더에서 시작한다.

API 서버에는 `HEATPIPE_API_TOKEN` 환경변수로 VTI 백엔드와 공유할 긴 임의 키를 설정한다. 보호된 API는 `Authorization: Bearer <키>` 헤더를 요구한다. `/health`, `/version`, `/docs`, `/openapi.json`은 공개한다. 키가 없으면 보호된 API는 `503 AUTH_NOT_CONFIGURED`를 반환한다. 외부 호출에는 HTTPS를 적용한다.

입력 Object Key `inputs/sample.att`는 `data/objects/inputs/sample.att`를 의미한다. 분석 워커는 참조된 파일을 작업 폴더에 `{monitoring_data_id}.att`와 `{monitoring_data_id}.atg`로 복사해 배치 파이프라인에 전달한다.

```powershell
$env:HEATPIPE_API_TOKEN = Read-Host -MaskInput 'VTI Bearer API key'
& '..\heat\Scripts\python.exe' -m uvicorn heatpipe_service.api.app:app --app-dir src --port 8001
```

다른 터미널에서:

```powershell
$env:PYTHONPATH = (Resolve-Path '.\src')
& '..\heat\Scripts\python.exe' -m heatpipe_service.jobs.worker
```

한 건만 처리하고 종료하려면 `--once`를 사용한다. 현재 분석 워커는 한 프로세스만 운용한다. API의 `POST /api/v1/analysis-jobs`에는 다음과 같이 Object Key를 전달한다.

```json
{
  "monitoring_data_id": "sample",
  "att": {"object_key": "inputs/sample.att"},
  "atg": {"object_key": "inputs/sample.atg"},
  "frame_range": {"start": 0, "end_exclusive": 100}
}
```

결과는 `results/{job_id}/analysis_result.json`, `analysis_candidates.csv`, `images/…png` Object Key로 저장된다. 분석 워커는 크기와 SHA-256이 요청에 있으면 다운로드 후 검증한다. URL 방식은 HTTPS와 `HEATPIPE_ALLOWED_SOURCE_HOSTS`에 등록한 호스트만 허용하고 리다이렉트는 허용하지 않는다.

## MinIO 설정

MinIO SDK는 Object Key로 VTI MinIO에 직접 접근하거나 결과를 MinIO에 저장할 때만 필요한 선택 의존성이다: `pip install -e '.[minio]'`. 연결 시 아래 환경변수를 분석 워커에 설정한다.

- `HEATPIPE_STORAGE=minio`
- `HEATPIPE_MINIO_ENDPOINT` (예: `minio.example.internal:9000`)
- `HEATPIPE_MINIO_BUCKET`
- `HEATPIPE_MINIO_ACCESS_KEY`
- `HEATPIPE_MINIO_SECRET_KEY`
- `HEATPIPE_MINIO_SECURE=true` (HTTPS가 없는 로컬 실습 환경에서만 `false`)

MinIO 객체 다운로드·업로드는 Python SDK의 `fget_object`와 `fput_object`를 사용한다. 실제 MinIO 서버 연동 테스트는 접속 정보와 테스트 버킷이 준비되면 수행한다.

## 범위

분석 워커는 SQLite에서 대기 작업을 원자적으로 선점하고 ATT·ATG를 로컬 작업 폴더로 가져와 배치를 실행한다. 진행률은 INT-004에 반영한다. 성공 결과와 PNG는 저장소에 기록하고 결과 Object Key는 SQLite에 남긴다. INT-001은 완료 결과를 읽어 JSON을 반환한다. 후보 PNG는 같은 Bearer 인증을 적용한 `GET /api/v1/analysis-jobs/{job_id}/images/{candidate_id}`로 제공한다.

각 작업 시도가 끝나면 작업 폴더의 다운로드된 ATT·ATG와 임시 분석 출력은 제거한다. 결과 저장소 `data/objects`에 기록된 JSON·CSV·PNG는 유지한다. 제거에 실패하면 로그에 남겨 운영자가 확인한다.
