# Heat Pipe Service

서울에너지공사 열수송관 상태정보 관리서비스에 제공할 DaonH&S 열화상 이상징후 분석 서비스입니다.

이 디렉터리는 배포 대상 코드만 포함합니다. 기존 `../heat-pipe`는 실험과 결과 비교를 위한 PoC로 유지하며 운영 서비스가 런타임에 PoC를 import하지 않습니다.

## 현재 단계

- 검증된 ATT·ATG 파서 이관
- 열이상 점수맵·후보 추출·트래킹·순위화 이관
- ROI·열화상 렌더링 유틸리티 이관
- PoC 수치 동등성 Golden Test
- ATT·ATG → JSON·CSV·PNG 단일 배치 분석

현재 VTI 연동 범위는
[docs/INT_001_003_004_DEVELOPMENT_SCOPE.md](docs/INT_001_003_004_DEVELOPMENT_SCOPE.md)를
기준으로 합니다. 문서별 상태는 [docs/README.md](docs/README.md)에서 확인할 수 있습니다.

세부 이관 범위와 검증 결과는 [docs/CORE_MIGRATION.md](docs/CORE_MIGRATION.md)에 기록합니다.

INT-001·003·004 요청·응답 모델, FastAPI API, SQLite 작업 상태 저장소, 로컬·선택적 MinIO 저장소와 단일 작업 실행기를 제공합니다. 완료 작업의 결과 JSON과 후보 PNG는 DaonH&S API로 조회할 수 있습니다.
GIS·관로·SHP 처리와 INT-002·005는 VTI 담당으로 DaonH&S 개발 범위에서 제외합니다.

배치 파이프라인과 결과 계약 초안은
[docs/BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md](docs/BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md)를 참고합니다.

## 테스트

프로젝트의 기존 `heat` 가상환경을 사용하는 경우 저장소 루트에서 다음과 같이 실행합니다.

```powershell
cd heat-pipe-service
& '..\heat\Scripts\python.exe' -m pytest -q
```

실제 ATT·ATG 한 건을 운영 서버와 분리된 임시 DB·저장소에서 검증하려면 다음 스크립트를 사용합니다. 원본은 읽기 전용이며, 분석 구간을 작게 잡아도 ATT 전체 파일을 임시 복사하므로 여유 디스크 공간이 필요합니다. 임시 작업 파일과 결과는 종료 시 제거됩니다.

```powershell
$env:PYTHONPATH = (Resolve-Path '.\src')
$attPath = Read-Host 'ATT 파일 경로'
$atgPath = Read-Host 'ATG 파일 경로'
& '..\heat\Scripts\python.exe' scripts/smoke_real_job.py --att $attPath --atg $atgPath --frames 30
```

## API 및 OpenAPI

```powershell
cd heat-pipe-service
& '..\heat\Scripts\python.exe' -m pip install -e '.[test]'
$env:HEATPIPE_API_TOKEN = Read-Host -MaskInput 'VTI Bearer API key'
& '..\heat\Scripts\python.exe' -m uvicorn heatpipe_service.api.app:app --app-dir src --port 8001
```

- Swagger UI: `http://127.0.0.1:8001/docs`
- OpenAPI JSON: `http://127.0.0.1:8001/openapi.json`
- 위 두 주소는 서버 실행 중에 열리는 URL이며 프로젝트 폴더의 파일 경로가 아닙니다. 폴더에서 확인할 수 있는 OpenAPI 명세 사본은 [`docs/openapi.json`](docs/openapi.json)입니다. 코드 변경 후에는 다시 내보내야 합니다.
- Q15 검토용 PDF는 Windows의 맑은 고딕 글꼴과 선택 의존성 `pip install -e '.[pdf]'`을 준비한 뒤 `python scripts/build_q15_pdf.py`로 생성합니다. 생성된 PDF는 `output/pdf/`에 저장되며 Git에 포함하지 않습니다.
- `POST /api/v1/analysis-jobs`는 SQLite에 작업을 저장하고 `QUEUED`를 반환합니다. 별도 작업 실행기를 시작하면 파일 다운로드와 분석이 진행됩니다.
- `GET /api/v1/analysis-jobs/{job_id}`는 SQLite에 저장된 상태를 반환합니다. 서버를 재시작해도 접수된 작업이 남습니다.
- `GET /api/v1/analysis-jobs/{job_id}/results`는 완료 작업의 결과 JSON을 반환합니다. 후보의 `result_image.path`는 같은 API의 PNG 조회 경로입니다. 미완료 작업은 `409 RESULT_NOT_READY`입니다.
- `GET /api/v1/analysis-jobs/{job_id}/images/{candidate_id}`는 해당 후보 PNG를 `image/png`로 반환합니다. 문서 예시 작업의 PNG 파일은 제공하지 않습니다.
- `GET /health`, `GET /version`과 문서 화면은 인증 없이 제공합니다. INT-001·003·004 및 PNG 조회에는 `Authorization: Bearer <키>`가 필요합니다. 키가 설정되지 않으면 보호된 API는 `503 AUTH_NOT_CONFIGURED`를 반환합니다. Swagger UI의 **Authorize** 버튼에 키를 입력해 시험할 수 있습니다.

작업 DB의 기본 위치는 `data/jobs.sqlite3`이며 `HEATPIPE_JOB_DB_PATH` 환경변수로 변경할 수 있습니다. 현재는 단일 서버·단일 작업 실행기 구성용입니다. 상세 상태 전이·진행률·재시도 정책은 [docs/JOB_STATE_V0.md](docs/JOB_STATE_V0.md)를 참고하세요. 실행 중인 서버는 새 코드를 반영하려면 재시작해야 합니다.

Bearer 키는 VTI 백엔드와 DaonH&S 서버만 공유하고 소스 코드나 프런트엔드에 넣지 않습니다. 외부 연동에는 HTTPS를 사용합니다. VTI 화면의 PNG는 VTI 백엔드가 인증 헤더를 붙여 조회한 뒤 제공하는 방식이 기본안입니다.

작업 실행기는 **별도 터미널**에서 실행합니다. 기본 로컬 저장소는 `data/objects`의 Object Key를 사용하며 입력 파일을 그 아래에 두어야 합니다.

```powershell
& '..\heat\Scripts\python.exe' -m heatpipe_service.jobs.worker
```

VTI가 파일을 업로드한 뒤 참조정보를 보내는 흐름, 파일 배치와 선택적 MinIO 설정은 [docs/STORAGE_WORKER_V0.md](docs/STORAGE_WORKER_V0.md)에 있습니다. VTI가 Presigned GET URL을 제공하면 원본을 읽기 위한 MinIO 계정은 필요하지 않습니다.

계약 모델은 [src/heatpipe_service/api/models.py](src/heatpipe_service/api/models.py)에 있습니다. 현재 입력은 프레임 구간과 `x/y/width/height` ROI를 사용하며 ATT·ATG는 각각 Object Key 또는 URL 중 하나로 참조합니다. ROI를 생략하면 원본 영상 2×3 격자의 하단 가운데 칸만 분석합니다. 결과 PNG에는 그 분석 영역을 빨간색으로 표시합니다. 결과 모델은 기존 JSON Schema v0의 필드 구조를 따릅니다.

배치 실행 예시는 다음과 같습니다.

```powershell
$env:PYTHONPATH = (Resolve-Path '.\src')
$attPath = Read-Host 'ATT 파일 경로'
$atgPath = Read-Host 'ATG 파일 경로'
$monitoringId = [System.IO.Path]::GetFileNameWithoutExtension($attPath)
& '..\heat\Scripts\python.exe' -m heatpipe_service.jobs.batch `
  --job-id JOB-20261030-000001 `
  --monitoring-data-id $monitoringId `
  --att $attPath `
  --atg $atgPath `
  --output .\output\sample
```

