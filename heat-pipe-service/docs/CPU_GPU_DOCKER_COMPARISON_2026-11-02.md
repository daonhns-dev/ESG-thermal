# 실제 ATT·ATG 전체 프레임 Docker CPU·GPU 비교

- 실행일: 2026-11-02
- 동일 입력: `20260813_132634.att` 875,667,464 bytes, `.atg` 151,552 bytes
- 동일 경로: INT-003 → 분석 워커 → INT-004 → INT-001 → PNG API
- 동일 환경: 이 PC의 WSL Ubuntu Docker, 같은 API·SQLite·공유 저장소·ROI·기본 분석 설정
- 달라진 항목: CPU 분석 워커 이미지와 CUDA 분석 워커 이미지

| 항목 | CPU Docker | GPU Docker |
|---|---:|---:|
| 작업 ID | `JOB-20261102-000005` | `JOB-20261102-000004` |
| 배치 `elapsed_seconds` | 427.246초 | 95.227초 |
| 처리 프레임 | 3,959 | 3,959 |
| 후보·PNG·CSV 행 | 50·50·50 | 50·50·50 |
| 상태·진행률 | `SUCCEEDED`·100% | `SUCCEEDED`·100% |

이 환경의 **배치 분석 시간**에서 GPU가 CPU보다 `427.246 / 95.227 ≈ 4.49배` 빨랐다. 절약한 배치 시간은 약 332.019초(5분 32초)다. 시간에는 입력 파일의 Docker 저장소 복사와 HTTP 요청·응답이 포함되지 않는다. GPU 분석 워커는 앞선 작은 작업으로 CUDA 커널이 준비된 상태였다. 최초 GPU 작업의 준비 비용과 다른 서버 사양에서는 차이가 달라질 수 있다.

후보 50건 모두 대표 프레임, 후보 사각형, ROI 온도, 후보 사각형 온도, 최대 이상점수, 경고가 일치했다. CPU·GPU 결과 JSON은 각각 JSON Schema v0을 통과했으며 PNG 50장과 CSV 50행을 확인했다. CPU 작업 임시 폴더는 제거됐다.

로컬 산출물은 Git에 포함하지 않는다.

- [CPU 결과 JSON](../output/full-frame-cpu-docker-20261102/analysis_result.json) · [CSV](../output/full-frame-cpu-docker-20261102/analysis_candidates.csv) · [첫 PNG](../output/full-frame-cpu-docker-20261102/images/20260813_132634_JOB-20261102-000005-CAND-0001_f001667.png)
- [GPU 결과 JSON](../output/full-frame-gpu-docker-20261102/analysis_result.json) · [CSV](../output/full-frame-gpu-docker-20261102/analysis_candidates.csv) · [첫 PNG](../output/full-frame-gpu-docker-20261102/images/20260813_132634_JOB-20261102-000004-CAND-0001_f001667.png)

후보는 관로 이상 확정 결과가 아니다. VTI 시험 서버에서의 성능과 Presigned HTTPS URL 다운로드는 별도로 검증해야 한다.
