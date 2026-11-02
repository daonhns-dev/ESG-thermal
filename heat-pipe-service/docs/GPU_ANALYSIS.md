# 선택형 GPU 분석

기준일: 2026-11-02. 기본 Compose 파일은 CPU를 사용한다. GPU가 있는 **DaonH&S 시험 서버의 목표 구성은 `compose.gpu.yaml`을 함께 사용하는 CUDA 분석 워커**다. GPU가 없는 개발 환경은 CPU 구성을 유지한다. INT-001·003·004 요청·응답 모델과 JSON Schema는 변경하지 않는다.

## 처리 범위

현재 `robust_zscore`, `top_hat_zscore`, `multiscale_top_hat`, `percentile_contrast`의 **ROI 점수맵 계산**을 CuPy와 `cupyx.scipy.ndimage`로 GPU에서 실행한다. ATT 읽기, 후보 연결 요소 추출, 추적, 순위화, ROI 온도 통계, PNG 생성은 CPU에서 수행한다. 따라서 전체 분석이 GPU로 옮겨진 것은 아니다. 향후 AI 모델의 GPU 추론은 모델을 선택한 뒤 별도로 구현·검증한다.

CPU가 기본값이다. `HEATPIPE_ANALYSIS_DEVICE=cuda`를 지정하면 GPU 또는 CUDA 패키지가 없을 때 분석 워커 시작 단계에서 오류가 난다. GPU 요청을 CPU로 조용히 바꾸지 않는다.

## Docker 실행

NVIDIA 드라이버와 Docker의 GPU 지원이 준비된 호스트에서 저장소 루트의 `.env`에 Bearer 키를 설정한다. CPU API 이미지를 먼저 빌드한 뒤 GPU 분석 워커를 빌드·실행한다.

서버에서 `nvidia-smi`로 호스트 GPU를 확인하고, 아래 첫 번째 `docker compose build api` 후 `docker run --rm --gpus all --entrypoint nvidia-smi heatpipe-service:local`로 컨테이너 GPU를 확인한다. 로컬 RTX 4070 결과만으로 다른 서버의 드라이버·GPU 호환성을 보장하지 않는다.

```sh
docker compose build api
docker compose -f compose.yaml -f compose.gpu.yaml build worker
docker compose -f compose.yaml -f compose.gpu.yaml up -d --no-build
docker compose -f compose.yaml -f compose.gpu.yaml ps
docker compose -f compose.yaml -f compose.gpu.yaml exec worker python -c "from heatpipe_service.analysis.gpu import require_cuda; require_cuda(); print('cuda_ready')"
```

GPU 이미지는 CuPy CUDA 13 선택 의존성을 포함하고 `gpus: all`은 **분석 워커**에만 적용한다. GPU가 없는 시험 서버에서는 기본 `docker compose up -d --build`를 사용한다. 로컬 배치 CLI는 `--device cuda`를 줄 수 있다. CPU로 돌아가려면 기본 Compose 파일만 사용해 `docker compose up -d --no-build worker`를 실행한다.

## 로컬 검증

RTX 4070 Laptop GPU, WSL Ubuntu Docker에서 GPU 이미지와 컨테이너 장치 전달을 확인했다. `scripts/check_gpu.py`를 GPU 이미지에서 실행해 144×128 ROI 점수맵 네 알고리즘을 CPU와 비교했다. 네 결과 모두 3.0 임계값 마스크가 같았고 최대 절대 점수 차이는 `robust_zscore`의 0.004612였다.

합성 ATT·ATG 4프레임을 API→GPU 분석 워커→INT-004→INT-001→PNG 경로로 실행했다. `JOB-20261102-000002`와 재실행 `JOB-20261102-000003`이 모두 `SUCCEEDED`, 후보 1건이었다. CPU 작업 `JOB-20261102-000001`과 후보 ROI·사각형·ROI 온도 통계가 같았다.

| 4프레임 배치 | 소요 시간 |
|---|---:|
| CPU | 0.153초 |
| GPU 첫 실행 | 17.596초 |
| GPU 같은 워커 재실행 | 0.177초 |

처음에는 CUDA 커널 준비 비용이 크고, 워밍업 뒤에도 이 작은 입력에서는 전체 작업이 CPU보다 빠르지 않았다. 점수맵 함수만 20회 반복하면 CPU 0.088초, GPU 0.023초였으나 이는 전체 처리 시간이 아니다.

이후 실제 ATT·ATG 전체 3,959프레임을 GPU Docker API로 실행해 95.227초에 완료했다. 같은 Docker 환경에서 CPU 분석 워커로 다시 실행한 시간은 427.246초로, 이 환경의 배치 분석은 GPU가 약 4.49배 빨랐다. 후보 50건의 위치·온도·최대 이상점수는 일치했다. [전체 GPU 검증 기록](REAL_DATA_GPU_DOCKER_2026-11-02.md)과 [CPU·GPU Docker 비교](CPU_GPU_DOCKER_COMPARISON_2026-11-02.md)에 결과와 산출물을 기록했다.

[CuPy 설치 문서](https://docs.cupy.dev/en/stable/install.html)와 [Docker Compose GPU 문서](https://docs.docker.com/compose/how-tos/gpu-support/)의 구성에 맞춰 선택 의존성과 GPU 장치 지정을 사용한다.
