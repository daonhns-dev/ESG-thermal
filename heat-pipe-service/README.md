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

INT-001·003·004 API, 파일 저장소 연동과 작업 큐는 이후 단계에서 추가합니다.
GIS·관로·SHP 처리와 INT-002·005는 VTI 담당으로 DaonH&S 개발 범위에서 제외합니다.

배치 파이프라인과 결과 계약 초안은
[docs/BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md](docs/BATCH_PIPELINE_AND_RESULT_SCHEMA_V0.md)를 참고합니다.

## 테스트

프로젝트의 기존 `heat` 가상환경을 사용하는 경우 저장소 루트에서 다음과 같이 실행합니다.

```powershell
cd heat-pipe-service
& '..\heat\Scripts\python.exe' -m pytest -q
```

배치 실행 예시는 다음과 같습니다.

```powershell
$env:PYTHONPATH = (Resolve-Path '.\src')
& '..\heat\Scripts\python.exe' -m heatpipe_service.jobs.batch `
  --job-id JOB-20261030-000001 `
  --monitoring-data-id 20260813_132634 `
  --att E:\heatpipe\20260813\20260813_132634.att `
  --atg E:\heatpipe\20260813\20260813_132634.atg `
  --output .\output\20260813_132634
```

