# 전체 프레임 후보별 ROI 온도 검증

산출물은 Git에 포함하지 않는다. 아래 `output/` 링크는 검증을 실행한 로컬 작업 폴더에서만 열린다.

- 실행일: 2026-11-02
- 입력: 외장하드 `E:\heatpipe\20260813\20260813_132634.att`와 같은 이름의 `.atg` (원본 읽기 전용)
- 작업 ID: `JOB-20261102-000004` (배치 검증용)
- 분석 ROI: 384×288 영상의 2×3 격자 하단 가운데 칸 `x=128,y=144,width=128,height=144`
- 실행 방식: 배치 CLI 전체 3,959프레임, 새 출력 디렉터리에 저장

## 결과

| 항목 | 확인 결과 |
|---|---:|
| 배치 종료 상태 | `SUCCEEDED` |
| 실제 소요 시간 | 439.75초 (약 7분 20초) |
| 처리한 ATT 프레임 | 3,959 |
| 후보 / PNG / CSV 행 | 50 / 50 / 50 |
| `roi_temperature_c` 포함 | 후보 50건 모두 |
| JSON Schema v0 및 Pydantic 모델 | 통과 |
| PNG 파일 검증 | 50장 통과 |

`roi_temperature_c`는 **각 후보 대표 프레임의 ATT 픽셀 온도**에서 분석 ROI 전체를 잘라 계산한다. `minimum`, `mean`, `maximum`은 °C이며, `max_minus_min`과 `max_minus_mean`은 표시된 두 값의 차이다. 기존 `temperature_c`는 후보 사각형의 온도와 주변 배경 대비 통계이므로 의미가 다르다. ATG는 온도값에 사용하지 않고 촬영시각과 GPS에 사용한다.

1순위 후보의 대표 프레임 1,667을 ATT에서 다시 읽어 대조한 값:

```json
{
  "minimum": 35.64,
  "mean": 41.50,
  "maximum": 130.00,
  "max_minus_min": 94.36,
  "max_minus_mean": 88.50
}
```

이 수치는 실제 입력에서 나온 값이며 사용자 화면 예시의 20도대 숫자와는 다르다. [1순위 PNG](../output/full-frame-center-roi-temp-20261102/images/20260813_132634_JOB-20261102-000004-CAND-0001_f001667.png)에는 차량 부근 열점이 표시된다. **ROI 최고 온도를 관로의 최고 온도나 누수 확정값으로 해석하면 안 된다.** 차량 열원의 후보 제외·순위 품질은 별도 검토가 필요하다.

ATT 3,959프레임과 ATG 3,885레코드의 차이는 기존과 같이 `ATT_ATG_COUNT_MISMATCH` 경고로 남는다. 새 필드는 이전에 저장한 결과에는 없을 수 있도록 선택 항목으로 추가했다.

## 산출물

- [ROI 온도 포함 전체 결과 JSON](../output/full-frame-center-roi-temp-20261102/analysis_result.json)
- [후보 CSV](../output/full-frame-center-roi-temp-20261102/analysis_candidates.csv)
- [1순위 PNG](../output/full-frame-center-roi-temp-20261102/images/20260813_132634_JOB-20261102-000004-CAND-0001_f001667.png)

이번 실행은 배치 CLI 검증이다. 새 필드가 작업 실행기와 INT-001 HTTP 응답까지 전달되는지는 단위·통합 테스트로 확인했다. 실제 VTI 연동 시험은 아직 수행하지 않았다.
