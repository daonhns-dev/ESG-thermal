# ATT·ATG HTTPS URL 다운로드 로컬 검증

- 실행일: 2026-11-02
- 환경: WSL Ubuntu Docker, GPU 분석 워커, 컨테이너 내부의 임시 HTTPS 파일 서버
- 목적: INT-003의 `att.url`·`atg.url` → HTTPS 다운로드 → 파일 크기 검증 → 분석 → INT-004/001/PNG 흐름 확인

테스트 서버는 `scripts/https_file_fixture.py`와 `compose.url-test.yaml`로만 구성했다. 파일별 서명과 만료 시각을 확인하는 **로컬 모의 URL**이며 VTI MinIO 또는 실제 Presigned URL 구현은 아니다. 인증서는 로컬에서 생성한 임시 인증서이고 분석 워커의 테스트 환경에만 신뢰시켰다. 테스트 서버는 외부 포트를 열지 않았다.

## 결과

| 작업 | 입력·조건 | 결과 |
|---|---|---|
| `JOB-20261102-000006` | 합성 ATT·ATG의 유효한 서명 HTTPS URL | `SUCCEEDED`, 4프레임, 후보 1건, PNG 조회 |
| `JOB-20261102-000007` | 실제 ATT 875,667,464 bytes + ATG 151,552 bytes의 유효한 서명 HTTPS URL | `SUCCEEDED`, 3,959프레임, 후보 50건·PNG 50장·CSV 50행 |
| `JOB-20261102-000008` | 요청 ATT 크기를 실제보다 1 byte 크게 지정 | 최종 `FAILED`, `FILE_INTEGRITY_ERROR` |
| `JOB-20261102-000009` | 허용 목록에 없는 URL 호스트 | 최종 `FAILED`, `SOURCE_URL_NOT_ALLOWED` |
| `JOB-20261102-000010` | 이미 만료된 서명 URL | 3회 시도 후 최종 `FAILED`, `DOWNLOAD_FAILED`; 임시 파일 제거 |

실제 파일 작업의 `processing.elapsed_seconds`는 91.362초이며, 작업 생성부터 완료까지는 약 94.9초였다. 이 시간은 로컬 Docker 네트워크와 공유 디스크에서 측정한 것으로 VTI MinIO 네트워크 성능을 뜻하지 않는다. 3,959프레임 결과는 기존 [GPU 저장소 입력 결과](REAL_DATA_GPU_DOCKER_2026-11-02.md)와 후보 50건의 대표 프레임·사각형·ROI 온도·후보 온도와 경고가 일치했다. JSON Schema v0, PNG 50장, CSV 50행을 확인했다. 전체 작업의 임시 폴더도 제거됐다.

- [실제 URL 입력 결과 JSON](../output/full-frame-https-url-20261102/analysis_result.json)
- [후보 CSV](../output/full-frame-https-url-20261102/analysis_candidates.csv)
- [첫 후보 PNG](../output/full-frame-https-url-20261102/images/20260813_132634_JOB-20261102-000007-CAND-0001_f001667.png)

산출물은 이 PC의 `output/`에 있으며 Git에 포함하지 않는다. VTI가 실제 URL을 발급하면 **VTI 저장소 주소의 접근 가능 여부, 인증서, URL 유효기간, 875MB 다운로드, 오류 응답**을 시험 서버에서 다시 확인해야 한다.
