# 2×3 격자 하단 가운데 ROI 전체 프레임 재검증

산출물은 Git에 포함하지 않는다. 아래 `output/` 링크는 검증을 실행한 로컬 작업 폴더에서만 열린다.

- 실행일: 2026-11-02
- 입력: `E:\heatpipe\20260813\20260813_132634.att`와 같은 이름의 `.atg`, 원본 읽기 전용
- 작업 ID: `JOB-20261102-000003` (배치 검증용)
- 출력: `output/full-frame-center-roi-20261102/`
- 목적: [이전 하단 전체 폭 ROI 실행](REAL_DATA_FULL_2026-11-02.md)을 수정된 기본 ROI로 다시 검증

## 확인 결과

| 항목 | 결과 |
|---|---:|
| 전체 ATT 프레임 / 유효 처리 프레임 | 3,959 / 3,959 |
| 기본 분석 ROI (`x,y,width,height`) | `128,144,128,144` |
| 원본 영상 해상도 | 384×288 |
| 실제 소요 시간 | 465.11초 (7분 45초) |
| JSON 기록 배치 소요 시간 | 464.467초 |
| 후보 / PNG / CSV 행 | 50 / 50 / 50 |
| 후보 좌표 | 50건 모두 분석 ROI 내부 |
| 결과 JSON Schema v0 | 검증 통과 |
| PNG 파일 | 50장 모두 파일 검증 통과 |
| 산출물 크기 | 2,976,963 bytes |

결과 PNG에는 2×3 흰 격자, **하단 가운데 칸의 빨간 분석 ROI**, 초록 후보 영역이 표시된다. [1순위 PNG](../output/full-frame-center-roi-20261102/images/20260813_132634_JOB-20261102-000003-CAND-0001_f001667.png)에서 직접 확인할 수 있다.

결과 JSON에는 `ATT_ATG_COUNT_MISMATCH` 경고가 있다(ATT 3,959프레임, ATG 3,885레코드). 후보 50건은 `max_results=50` 상한이며 관로 이상 확정 건수가 아니다. 상위 후보의 대표 프레임은 1,511~1,918에 집중되고, 대표 PNG에서는 차량 부근 열점이 보인다. **정해진 ROI 안에서 계산한다는 요구는 충족했지만, 차량 등의 열원을 제외하는 후보 품질 검토는 별도 과제**다.

## 산출물

- [결과 JSON](../output/full-frame-center-roi-20261102/analysis_result.json)
- [후보 CSV](../output/full-frame-center-roi-20261102/analysis_candidates.csv)
- [1순위 PNG](../output/full-frame-center-roi-20261102/images/20260813_132634_JOB-20261102-000003-CAND-0001_f001667.png)

이 실행은 배치 CLI에서 수행했다. API→분석 워커→HTTP 결과 조회 흐름은 앞서 [30프레임 검증](REAL_DATA_SMOKE_2026-11-02.md)으로 확인했다.
