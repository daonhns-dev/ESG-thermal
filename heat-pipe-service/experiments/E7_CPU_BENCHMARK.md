# E7 CPU 계측과 E0 후보 대조

로컬 Windows CPU에서 2026-08-13 ATT·ATG를 사용했다. 기준은 같은 작업 트리의 E0 배치 경로이며, 별도 Python 프로세스에서 하나씩 실행했다. 각 실행은 기본 ROI와 상위 50건 설정을 사용한다. 처리 시간에는 파일 읽기와 PNG·JSON·CSV 기록이 포함된다. 최대 RSS는 Windows의 프로세스 최대 작업 집합이며 메모리맵의 상주 페이지도 포함한다. 단계별 계측의 `percentile_contrast`는 `score_maps`에 포함된다.

## 360프레임 비교

| 변경 | 전체 초 | 최대 RSS MiB |
|---|---:|---:|
| E0 | 33.55 | 171.8 |
| ATT 메모리맵 | 34.19 | 247.3 |
| 기울기 재사용 | 33.79 | 172.7 |
| 메모리맵과 기울기 재사용 | 32.89 | 248.7 |
| 백분위 필터만 최적화 | 5.78 | 172.7 |
| 세 가지 결합 | 5.60 | 248.0 |

모든 실행에서 상한 전 적격 트랙 278건의 전체 내용과 순서, 최종 후보 순서, CSV 바이트와 PNG 해시가 E0와 일치했다. E0에서는 33.55초 중 30.21초가 점수맵 계산이었다. 백분위 필터만 최적화한 실험의 점수맵 계산은 2.89초였다. 온도값별로 윈도 내 누적 픽셀 수를 세어 SciPy의 백분위 순위와 경계 반사 방식을 그대로 계산한다. 고유값이 많거나 해당 계산 조건을 벗어나면 기존 필터를 호출한다.

## 전체 3,959프레임 비교

| 변경 | 전체 초 | 최대 RSS MiB | 백분위 대비 계산 초 |
|---|---:|---:|---:|
| E0 | 491.18 | 222.85 | 425.93 |
| 백분위 필터만 최적화 | 104.66 | 223.29 | 25.74 |
| 메모리맵·기울기 재사용·백분위 필터 결합 | 111.17 | 1,057.45 | 30.64 |

두 최적화안 모두 적격 트랙 2,918건의 전체 내용과 순서, 반환 후보 50건과 순서, CSV 바이트와 PNG 해시가 E0와 일치했다. 이 입력의 단일 실행에서 백분위 필터만 바꿨을 때 전체 시간이 **약 79% 감소**했고 최대 RSS 차이는 **0.44 MiB**였다. 다음 CPU 실험에서는 백분위 필터만 적용한다. 메모리맵을 포함한 조합은 이 실행에서 최대 작업 집합이 1,057 MiB였다. 다른 촬영 회차와 4 vCPU·4 GB 시험 서버에서 처리 시간과 메모리를 별도로 확인한다.

## 저장된 후보의 E0 대조

8월 11개와 10월 2개에서 저장된 E0·E1·E1+대표 ±6 결과를 비교했다. 총 131,295프레임에서 같은 입력 범위와 ROI를 확인했다. 파일별 50건씩, 각 방법의 650건 가운데 대표 프레임과 사각형이 정확히 일치하는 건수는 E0↔E1 484건, E0↔E1+대표 ±6 381건, E1↔E1+대표 ±6 492건이었다. 사각형과 프레임의 일치만 센 값이며, 같은 열원 및 관로 이상 재현율은 사람 판독과 현장 정답으로 평가해야 한다.

## 고정된 E1·E3 조합 확인

360프레임에서 E1의 MAD 대체 처리와 E3 대표 ±6을 결합했다. 기존 백분위 필터는 41.006초, 최적화 필터는 5.856초였다. 두 실행의 적격 트랙 217건은 압축을 푼 전체 내용과 순서가 같았으며, 반환 50건, CSV, PNG 해시도 일치했다. 저장된 트랙을 별도로 대표 ±6 규칙에 넣어 배치가 실제로 동일한 50건을 선택했음을 확인했다. 결과는 `combined-pilot-check.json`에 있다.

## 재현 명령

`heat-pipe-service`에서 실행한다.

```powershell
$py = '..\heat\Scripts\python.exe'
$att = 'data/experiments/raw/20260813/20260813_132634.att'
$out = 'output/experiments/e7-e0-cpu'
& $py experiments/e7_benchmark.py $att "$out/full-e0"
& $py experiments/e7_benchmark.py $att "$out/full-all" --variant all --against "$out/full-e0"
& $py experiments/e7_benchmark.py $att "$out/full-percentile" --variant percentile --against "$out/full-e0"
& $py experiments/compare_e0_e3.py output/experiments/e3-development-20261117/selection-stationary-development.json "$out/e0-e1-e3-comparison.json"
```

고정된 E1·E3 후보 선정과 백분위 최적화를 한 번에 확인하는 실험 명령이다.

```powershell
& $py experiments/compare_methods.py --att $att --atg ([IO.Path]::ChangeExtension($att, '.atg')) --output "$out/e1-rep6-fast" --method nonzero_mad_fallback --device cpu --fast-percentile --representative-suppression
```

운영 기본값 평가는 새 촬영 회차에서 하고, 동등성 및 처리 성능은 시험 서버 CPU에서도 재계측한다.
