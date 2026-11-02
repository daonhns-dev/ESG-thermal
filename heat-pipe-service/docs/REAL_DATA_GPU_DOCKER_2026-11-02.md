# 실제 ATT·ATG 전체 프레임 GPU Docker API 검증

- 실행일: 2026-11-02
- 입력: 외장하드의 `20260813_132634.att`(875,667,464 bytes), `20260813_132634.atg`(151,552 bytes)를 Docker 공유 저장소에 복사. 원본은 읽기 전용.
- 작업 ID: `JOB-20261102-000004`
- 경로: INT-003 → GPU 분석 워커 → INT-004 → INT-001 → 후보 PNG API
- 분석 ROI: 원본 384×288의 2×3 격자 하단 가운데 칸

## 결과

| 항목 | 확인 결과 |
|---|---:|
| 최종 상태·진행률 | `SUCCEEDED`, 100% |
| 처리 프레임 | 3,959 |
| 후보·PNG·CSV 행 | 50·50·50 |
| 배치 분석 시간 | 95.227초 |
| JSON Schema v0 | 통과 |
| HTTP 결과·첫 PNG | 200, PNG 시그니처 확인 |
| 작업 임시 폴더 | 제거 확인 |

기존 CPU 배치 CLI 결과는 439.077초였다. 측정값의 비율은 약 4.61이지만, **CPU는 Windows 로컬 Python 배치 CLI, GPU는 WSL Docker API·분석 워커**에서 실행했으므로 동일 환경의 엄밀한 속도 비교는 아니다. Docker 작업의 배치 시간에는 컨테이너 간 파일 복사·API 응답 시간은 포함되지 않는다.

이후 같은 WSL Docker 환경에서 CPU 분석 워커로 재실행해 427.246초를 측정했다. 동일 환경의 비교는 [CPU·GPU Docker 비교 기록](CPU_GPU_DOCKER_COMPARISON_2026-11-02.md)을 따른다.

기존 CPU 결과와 GPU 결과는 후보 50건 모두 **대표 프레임·후보 사각형·ROI 온도·후보 사각형 온도·최대 이상점수**가 일치했다. 첫 후보는 프레임 1,667이며 ROI 최저 35.64℃, 평균 41.50℃, 최고 130.00℃다. 차량 부근 열점이 상위 후보인 점은 이전과 같다. 3,959 ATT 프레임과 3,885 ATG 레코드 차이는 `ATT_ATG_COUNT_MISMATCH` 경고로 기록됐다. 후보는 관로 이상 확정 결과가 아니며 품질 검토가 남아 있다.

## 로컬 산출물

아래 파일은 Git에 포함하지 않으며 이 PC의 `output/`에 있다.

- [GPU 결과 JSON](../output/full-frame-gpu-docker-20261102/analysis_result.json)
- [후보 CSV](../output/full-frame-gpu-docker-20261102/analysis_candidates.csv)
- [첫 후보 PNG](../output/full-frame-gpu-docker-20261102/images/20260813_132634_JOB-20261102-000004-CAND-0001_f001667.png)

이 검증은 로컬 Object Key 입력을 사용했다. VTI MinIO의 Presigned HTTPS URL 다운로드와 실제 시험 서버 연결은 별도 연동 시험 항목이다.
