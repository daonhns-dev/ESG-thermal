# E3 중복 억제 보완 및 사람 판독

## 조정에 사용한 자료

8월 전체 구간이 유효한 11쌍과 10월 26일·29일 실증 2쌍은 조정용이다. 저장된 E1 전체 적격 트랙을 재사용한다. 규칙은 실험 설정에서 고정하며 운영 적용 여부는 주간 촬영을 포함한 새 회차에서 판단한다. 관로 GIS와 새 촬영 회차 확보는 별도 요청 사항이다.

## 첫 사람 판독

`output/experiments/e3-human-review-20261117/reviewer/REVIEW.md`에서 판독한다. 시트 69장에 후보 카드 414개를 섞었다. 후보 전후 프레임 414장과 H043·H025가 겹친 시점의 추가 그림 두 장이 있다. 판독자에게는 `reviewer` 폴더만 전달한다. 방식·순위·촬영 파일·기존 라벨과 대응표는 `internal`에 둔다.

- 10월 노면 후보 4건, H043/H025, 8월에 대체로 집계된 노면 트랙 4건과 그 상대 후보, 정차 반복 사례 및 비교용 차량 반복 사례를 포함한다. 8월 대체 트랙 하나에 복수 상대가 있어 최종 쌍 질문은 15개다.
- 파일마다 상한 전 E1 지속성 점수순 51~300위 중, 실제 10구간 순환 상위 50건과 겹치는 트랙을 제외하고 30건을 무작위로 뽑았다. 총 390건이다. 파일별 노면 비율은 이 30건 중 사람 판독이 `road`인 수로 계산한다. `cover`와 `uncertain`은 별도 집계한다. 전체 비율은 같은 크기로 뽑은 표본을 합산한 값이다.
- `candidate_labels.json`에는 차량, 노면 열점, 맨홀·덮개, 기타, 판단 불가, 영상 이상을 기록한다. `pair_labels.json`에서는 동일 열원 여부, A의 열점이 B 대표 이미지에 보이는지와 그 반대 방향, A와 B 각각의 덮개 해당 여부를 기록한다. 혼합 트랙의 일부분이 겹친 경우 관찰 근거를 비고에 남긴다.
- 쌍 15개와 개별 후보 R001~R020의 1차 사람 판독이 완료됐다. 쌍 판독은 그대로 사용한다. 1차 개별 답은 `internal/candidate_labels_R001-R020_round1.json`에 보존했다. 판독자는 `REVIEW.md`의 라벨 정의를 적용해 `reviewer/candidate_labels_R001-R020_round2.json`에 2차 답을 기록한다. `agreement`로 전체 일치 건수, 라벨 전이, 변경 후보 ID를 기록한다. 390건 표본 판독과 노면 비율 산출은 재판독 기준을 확정한 뒤 진행한다.

## 라벨 정의 변경 전후 비교

같은 사람이 기존 정의와 수정한 정의를 적용해 R001~R020을 판독했다. 일치한 라벨은 **11/20건(55%)**이었다. 기존 `road` 13건 중 4건이 유지됐고, 8건은 `uncertain`, 1건은 `cover`로 변경됐다. `vehicle` 7건은 모두 유지됐다. 이 비교는 정의 변경 전후의 결과다.

- 확대 전 화면의 판단 불가율은 **8/20(40%)**였다. 운영 화면에서 검토자가 판단하기 어려울 비율의 예비 추정치로 기록한다. 목적 표본이 섞인 20건에서 한 사람이 판독한 값이므로 실제 운영 전체로 일반화하기에는 자료가 적다.
- 1~20번 중 51~300위 무작위 표본에 속하는 18건에서 노면은 **3/18건(16.7%, R004·R007·R020)**이었다. 95% 정확 이항 구간은 약 **4~41%**다. R008과 R012는 우선 검토 사례이며 무작위 표본의 분모에서 제외한다.
- 8월 E1 상위 50건의 기존 판독에서 노면 비율은 **10~16%**였다. 표본 크기와 판독 기준이 다른 예비 비교다. **지속성 점수가 노면 열점을 상위에 모으지 못할 수 있다**는 가설을 남긴다.

## 확대 이미지 판독과 표본 형식

2차에서 `road`가 `uncertain`으로 바뀐 8건만 먼저 확인한다. 후보 주변을 확대하고 각 후보의 5개 프레임에 같은 온도 범위를 적용한다. `reviewer/zoom-pilot/zoom_labels.json`에 원래 화면의 `original_label`과 확대 판독의 `zoom_label`을 따로 둔다. 사람 판독 완료 후 `zoom-score`로 `uncertain`에서 `road`·`cover`·`other`로 전환된 비율을 기록한다.

8건 판독 후 원래 이미지 또는 원래 이미지와 확대를 함께 제공하는 형식 중 하나를 정하고, 나머지 표본에 동일하게 적용한다. 처음 표본화한 390건 중 R001~R020에 포함된 18건을 제외하면 **추가 판독은 372건**이다. 현재 `REVIEW.md`의 여섯 라벨 정의를 적용한다. 판독 후 파일별 및 점수순 **51~100·101~200·201~300위**별로 노면 건수와 분모를 기록하고 상위 50건의 기존 비율과 대조한다. 확대 포함 형식으로 결정되면 원래 이미지로 판독한 예비 18건은 별도 표기한다. 순위 구간별 비율이 비슷하면 E3 이후 지속성 점수의 순위 개선을 후속 과제로 제안한다.

사람이 전체 장면과 확대를 대조한 결과, **7건은 `uncertain` 유지**, **R019만 `other`**로 변경됐다. `road`·`cover`·`other` 전환은 **1/8건(12.5%)**이었다. 이 표본에서 확대 후 새로 판독된 노면 열점은 0건이었다. 남은 372건은 **원래 5프레임 이미지 형식**으로 고정했으며, `reviewer/rank-sample/REVIEW.md`와 `labels.json`을 사용한다. 순위 구간 집계에는 기존 18건의 원래 이미지 기준 2차 판독을 사용하고, 확대 시험의 라벨은 별도 유지한다.

## 규칙 비교와 확인

초안은 기존 3프레임·8픽셀 접촉, 짧은 트랙의 50% 이상 접촉, 남길 후보의 대표 프레임 ±6 근처의 접촉, 두 조건 결합이다. 대표 프레임 조건은 H043/H025가 일부 접촉했어도 H043을 H025의 대표 이미지로 대체하지 않는지 먼저 확인한다. 차량 감점은 선택 비교로 남긴다.

정차 규칙은 별도로 표시한 정차 구간에서만 기존 시간 제한을 넓힌다. 두 대표 사각형과 그 주변에서 반복 관측한 사각형의 위치가 충분히 가까워야 한다. `--stationary`에는 파일별로 **확인한 정차 구간**의 시작 프레임과 끝 프레임(미포함)을 넣는다. JSON 형식은 `{"파일ID": [[시작, 끝], ...]}`이다. 자료가 없으면 결합 정차 방식의 결과는 계산하지 않는다.

기술 확인용으로 네 초안을 13개 전체 E1 트랙에서 실행했다. `selection-preview.json`의 수치는 라벨이나 규칙을 고정하기 전 확인용이다. H043/H025는 기존 조건에서는 접촉했고, 대표 프레임 조건과 50% 조건에서는 각각 제외 조건을 통과하지 못했다. 첫 판독과 정차 구간 확인 후 규칙을 짧은 구간에서 조정한다. 후보 규칙을 고정하기 전에 13개 전체 파일에서 다시 선정한다. 대표 프레임 조건을 적용해도 노면이 계속 빠지면 위치·면적 급변이나 큰 회전 구간에서 혼합 트랙을 분할하고, 주된 트랙 구간으로 대표 프레임을 정하는 안을 비교한다.

전체 파일의 출력은 기존 노면의 **트랙 ID가 그대로 선택된 수**, 빠진 트랙, 새 미판독 트랙을 기록한다. 과거 라벨은 재판독 대상을 찾는 데 쓰며 사람 확인 수와 분리한다. 사람 판독으로 확인한 실제 중복·다른 열원 억제·대표 이미지에서 숨겨진 열원은 구분한다. 판독이 일부라면 확인된 건수는 하한이며, 판독이 없는 항목은 빈 값이다. 규칙상 건너뛴 건수는 참고용이다.

### 빠진 트랙의 선정 경로

`selection-path-audit.json`에 기존 노면 트랙 4건과 P15의 두 트랙을 기록했다. 8/14의 4966(P07), 8/17의 3822(P10)·4218(P13/P14)·12080(P05)은 네 중복 억제안 모두에서 직접 억제됐다. 실제로 새로 남긴 상대 후보의 대표 이미지에 원래 열점이 보인다는 사람 판독이 네 건 모두에 있다. 4218의 상대는 기존 규칙에서 4054(P13), 나머지 세 규칙에서 4233(P14)이다. P05는 두 후보의 덮개 여부가 서로 다르게 기록돼 열원 유형 해석에 유의한다.

10/26의 P15에서는 R229(H043)가 전체 점수순 167위, 2구간 내 7위였다. R241(H025)은 전체 128위, 같은 구간 내 4위였다. E1과 네 중복 억제안 모두 해당 구간에서 5건을 뽑았고, R229를 검사하기 전에 50건을 채웠다. 이 재실행의 R229 누락 원인은 선정 상한이다. 이전 10월 비교에서 관찰된 두 트랙의 직접 접촉 문제는 별도 사례로 남는다.

현재 비교 1순위는 **대표 프레임 ±6 조건**이다. 같은 열원으로 사람 판독한 14쌍에서 두 후보가 모두 선택된 쌍이 E1 10쌍, 기존 접촉 규칙 7쌍, ±6 조건 3쌍이었다. 이 14쌍은 규칙 개선 방향을 확인하는 표본이다. 조정 자료 전체의 중복률이나 새 회차 성능은 별도 확인한다.

### 정차 반복 후보 비교

10/29의 1750~2700번 구간은 **2297번 프레임을 기준으로 직접 확인한 조정용 정차 구간**이다. 선택한 5개 영상 영역을 기준 프레임과 대조한 결과, 951프레임 모두에서 3개 이상의 영역이 정규화 상관 0.55 이상이고 각 축 이동이 1픽셀 이내였다. 확인 자료와 프레임 범위는 각각 `stationary-image-check.json`, `stationary-development-segments.json`에 있다.

8월 11개와 10월 2개 파일의 전체 E1 트랙에서 대표 ±6과 대표 ±6에 정차 예외를 더한 방식을 비교했다. 정차 쌍 7개 중 두 후보가 함께 선택된 쌍은 **3→2**였다. P09의 R090(2973)이 R195(2912)에 의해 억제됐고 같은 열원으로 직접 판독된 R072(3001)는 계속 선택됐다. R090과 실제 억제 상대 R195의 직접 판독은 없다. 상위 50건에는 미판독 트랙 3105가 새로 들어왔다. 나머지 12파일의 선정과 기존 노면 트랙의 정확한 ID 유지 수는 같았다. 10/26 P15의 선정 결과도 같았다.

대표 ±6을 실험 기준으로 두고 정차 예외는 선택 옵션으로 유지한다. 기본 적용 검토 전에는 `negligible_motion` 기반 판정이나 상관 검사를 촬영 전체에 적용해 **자동으로** 이 구간을 찾아내는지, 다른 12개 파일에서 주행 구간을 정차로 잘못 잡는지 확인한다. 지난 비교에서 12개 파일의 선정 결과가 같았던 것은 그 파일들에 정차 구간을 입력하지 않았기 때문이다. 자동 정차 판정의 오탐 여부는 아직 확인하지 않았다. **372건 사람 판독이 끝날 때까지 E3 규칙 추가 조정을 보류한다.** P15는 선정 상한에서 밀렸으므로 이 사례만으로 혼합 트랙 분할로 넘어가지 않는다. 결과는 `selection-stationary-development.json`에 저장했다.

R090/R195 쌍 질문(S01)과 트랙 3105의 유형 판독(R415)은 `reviewer/rank-sample/labels.json` 끝의 `supplemental`에 추가했다. 372건 순위 표본에는 별도로 집계한다.

`extra`는 새로 선정된 미판독 후보에만 **유형 판독**을 요청한다. 판독이 필요한 노면 제외 쌍은 같은 열원과 대표 이미지 노출을 확인하는 쌍 질문으로만 포함한다.

## 실행 명령

PowerShell에서 `heat-pipe-service`를 기준으로 실행한다.

```powershell
$py = '..\heat\Scripts\python.exe'
$review = 'output/experiments/e3-human-review-20261117'
$output = 'output/experiments/e3-development-20261117'
& $py experiments/e3_review.py agreement $review  # R001~R020 2차 판독 완료 후
& $py experiments/e3_review.py zoom $review
& $py experiments/e3_review.py zoom-score $review  # 확대판 8건 사람 판독 완료 후
& $py experiments/e3_review.py rank-sample $review
& $py experiments/e3_review.py rank-sample-score $review  # 표본 사람 판독 완료 후
& $py experiments/e3_review.py compare "$output/selection-stationary-development.json" --human-report "$output/human-round2-selection-input.json" --stationary "$output/stationary-development-segments.json"
& $py experiments/e3_review.py prepare $review --sample-per-file 30
& $py experiments/e3_review.py compare "$output/selection-preview.json"

# 사람이 두 양식을 완성한 뒤
& $py experiments/e3_review.py score $review
& $py experiments/e3_review.py compare "$output/selection-reviewed.json" --human-report "$review/internal/human_review_scored.json" --stationary 'C:\path\to\confirmed-stop-segments.json'
& $py experiments/e3_review.py extra "$output/extra-review" "$output/selection-reviewed.json"
& $py experiments/e3_review.py score "$output/extra-review"
& $py experiments/e3_review.py compare "$output/selection-reviewed-final.json" --human-report "$review/internal/human_review_scored.json" --human-report "$output/extra-review/internal/human_review_scored.json" --stationary 'C:\path\to\confirmed-stop-segments.json'
```

`prepare`과 `extra`는 새 출력 폴더를 요구한다. 10월 ATT가 외장하드에 있으면 해당 경로 읽기 권한이 필요하고, 다른 곳에 복사했으면 `--october-dir`을 지정한다. 아직 정차 구간을 확인하지 못했다면 `--stationary` 인자를 뺀다.
