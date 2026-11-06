# INT-001 결과 JSON 필드 설명서 v0

기준: 함께 송부하는 `analysis-result-v0.schema.json`, `schema_version: 0.3.0`. 이 문서는 T-01 회신용으로 JSON Schema의 필드 경로, 단위, 필수 여부 및 null 조건을 설명한다. 필드명과 구조의 기계 판독 기준은 JSON Schema다. `0.3.0`에서는 R2-02의 상한 및 상한 적용 전 적격 후보 수를 추가했다.

## 읽는 방법

- `candidates[]`는 배열의 각 후보 객체를 뜻한다. `[]` 배열은 허용되지만 `null`은 허용되지 않는다.
- 아래 표에서 **필수**는 해당 상위 객체가 존재할 때 그 속성을 반드시 포함한다는 뜻이다. **선택**은 속성 자체를 생략할 수 있다는 뜻이다. 필수 필드도 타입에 `null`이 있으면 값이 `null`일 수 있다.
- 결과 시각 `completed_at`은 서버가 생성한 UTC ISO 8601 시각이다. `captured_at`은 ATG에 기록된 촬영 시각을 한국 표준시로 해석하여 `+09:00` 오프셋을 붙인다. ATG 원본에는 시간대 정보가 없어 실제 장비의 시간대 설정은 DaonH&S가 확인해야 한다.
- 영상 좌표는 **원본 ATT 프레임의 왼쪽 위를 (0, 0)으로 하는 0부터 시작하는 픽셀 좌표**다. `x`, `y`는 시작 위치, `width`, `height`는 픽셀 개수다. GPS 위도·경도는 십진 도 단위다.
- 온도 값과 온도차는 모두 섭씨(°C)다. 후보·ROI 온도는 후보의 **대표 프레임**에서 계산한다. `roi_temperature_c`는 분석 ROI 전체, `temperature_c`는 후보 사각형 및 그 주변 배경을 나타낸다.
- INT-001은 `SUCCEEDED` 작업에서만 이 JSON을 HTTP 200으로 반환한다. 미완료·실패 작업은 HTTP 409이며 실패 원인은 INT-004에서 조회한다. JSON Schema의 `FAILED` 형태는 배치 내부 산출물도 검증하기 위해 포함한다.

## 최상위 필드

| JSON 경로 | 타입·단위 | 필수 | null·빈 값 조건 및 의미 |
|---|---|---|---|
| `schema_version` | 문자열, 현재 `0.3.0` | 예 | null·빈 값 불가. 결과 형식 버전. API 서버 버전과 별개다. |
| `job_id` | 문자열, `JOB-YYYYMMDD-NNNNNN` | 예 | null·빈 값 불가. DaonH&S 작업 ID. |
| `monitoring_data_id` | 문자열 | 예 | null·빈 값 불가. VTI가 요청에 보낸 회차 ID. INT-003 요청에서는 1~128자의 영문·숫자·`_`·`-`만 허용한다. |
| `status` | 문자열, `SUCCEEDED` 또는 `FAILED` | 예 | null 불가. INT-001 응답은 `SUCCEEDED`만 제공. |
| `success` | 불리언 | 예 | null 불가. 성공은 `true`, 실패는 `false`. |
| `completed_at` | 문자열, ISO 8601 UTC | 예 | null·빈 값 불가. 분석 종료 시각. |
| `processing` | 객체 또는 null | 예 | 성공 시 객체, 실패 시 null. 처리 범위·설정·소요 시간. |
| `detected_candidate_count` | 정수, 건, 또는 null | 예 | 최소 트래킹 프레임 수를 충족한 후보 트랙의 **상한 적용 전 수**. 새 성공 결과는 0 이상 정수. 실패 결과와 이전 저장 결과는 확인할 수 없어 null. 프레임별 원시 검출 수나 확정 이상 지점 수가 아니다. |
| `candidate_count` | 정수, 건 | 예 | null 불가. `candidates` 배열 길이. 현재 임시 기본 설정에서는 최대 50건을 반환하며 전체 탐지 건수를 뜻하지 않는다. UAT용 상한은 R2-01 실데이터 검토 후 확정한다. |
| `candidates` | 후보 객체 배열 | 예 | null 불가. 후보가 없거나 실패하면 `[]`. |
| `warnings` | `{code,message}` 객체 배열 | 예 | null 불가. 경고가 없으면 `[]`. 경고는 결과 상태와 별개다. 아래 데이터 품질 항목에 현재 코드와 사용 기준을 적었다. |
| `warnings[].code` | 문자열 | 예* | null 불가. 경고 코드. Schema는 빈 문자열을 별도로 금지하지 않는다. |
| `warnings[].message` | 문자열 | 예* | null 불가. 경고 설명. Schema는 빈 문자열을 별도로 금지하지 않는다. |
| `error` | `{code,message}` 객체 또는 null | 예 | 성공 시 null, 실패 시 객체. |
| `error.code` | 문자열 | 예* | `error`가 객체일 때 필수·null 불가. 오류 코드. Schema는 빈 문자열을 별도로 금지하지 않는다. |
| `error.message` | 문자열 | 예* | `error`가 객체일 때 필수·null 불가. 오류 설명. Schema는 빈 문자열을 별도로 금지하지 않는다. |

`예*`는 해당 배열 원소 또는 상위 객체가 존재할 때 필수다.

## `processing` 필드

아래 필드는 `processing` 객체가 존재할 때 필수이며 null이 될 수 없다.

| JSON 경로 | 타입·단위 | 의미 |
|---|---|---|
| `processing.input` | 객체 | 입력 파일 정보. |
| `processing.input.att_file_name` | 문자열 | 분석에 사용한 ATT 파일명. 빈 값 불가. |
| `processing.input.atg_file_name` | 문자열 | 분석에 사용한 ATG 파일명. 빈 값 불가. |
| `processing.frame_range` | 객체 | 실제 적용한 프레임 범위와 개수. |
| `processing.frame_range.start` | 정수, 0부터 시작하는 프레임 인덱스 | 분석 시작 프레임. |
| `processing.frame_range.end_exclusive` | 정수, 프레임 인덱스 | 이 값의 프레임은 포함하지 않는 끝 인덱스. |
| `processing.frame_range.stride` | 정수, 프레임 간격 | 처리할 프레임의 간격. |
| `processing.frame_range.total_att_frames` | 정수, 프레임 | ATT 파일의 전체 프레임 수. |
| `processing.frame_range.total_atg_records` | 정수, 레코드 | ATG 파일의 전체 레코드 수. 0 가능. |
| `processing.frame_range.processed_frame_count` | 정수, 프레임 | 실제 유효하게 분석한 프레임 수. 빈/잘못된 프레임은 제외하므로 0 가능. |
| `processing.analysis_roi` | ROI 객체, 픽셀 | 분석에 적용한 ROI. 구조는 아래 좌표 표 참조. |
| `processing.algorithms` | 문자열 배열 | 사용한 후보 생성 알고리즘 이름. 빈 배열 허용. |
| `processing.parameters` | 객체 | 사용한 분석 설정. |
| `processing.parameters.score_threshold` | 숫자, 알고리즘 점수 | 후보 판정 점수 기준. 온도(°C)가 아니다. |
| `processing.parameters.min_area_px` | 정수, 픽셀² | 후보의 최소 면적. |
| `processing.parameters.min_track_frames` | 정수, 프레임 | 후보 트랙의 최소 지속 프레임 수. |
| `processing.parameters.merge_iou_threshold` | 숫자, 0~1 | 같은 프레임 후보 병합의 IoU 기준. |
| `processing.parameters.max_results` | 정수, 1~1,000, 또는 null | 해당 작업에 적용한 반환 후보 상한. 새 성공 결과는 정수. 이전 저장 결과에서 확인할 수 없으면 null. 포털의 1,000건 허용 한도와 UAT용 설정값은 별개다. |
| `processing.elapsed_seconds` | 숫자, 초 | 배치 분석 소요 시간. 작업 대기와 API·네트워크 전송 시간은 포함하지 않는다. |

새 성공 결과에서 `detected_candidate_count > candidate_count`이면 상한으로 일부 후보가 반환되지 않았다. 두 값이 같으면 적격 후보 트랙은 모두 반환됐다. `detected_candidate_count`가 null인 이전 저장 결과에서는 잘림 여부를 판정할 수 없다.

## `candidates[]` 필드

각 후보 원소의 아래 상위 필드는 모두 **필수**다. 앞으로 INT-003으로 접수되는 작업의 성공 결과에서 `roi_temperature_c`는 항상 객체다. 이전 저장 결과에서 값이 없었던 경우에만 API가 `null`로 제공할 수 있다. `captured_at`과 `gps`는 명시된 조건에서 null이 될 수 있다.

| JSON 경로 | 타입·단위 | null·빈 값 조건 및 의미 |
|---|---|---|
| `candidates[].candidate_id` | 문자열, `JOB-…-CAND-NNNN` | null·빈 값 불가. DaonH&S가 생성. |
| `candidates[].rank` | 정수, 1부터 | null 불가. 결과 내 후보 우선순위. |
| `candidates[].representative_frame` | 정수, 0부터 시작하는 프레임 인덱스 | null 불가. 온도·위치·PNG의 기준 프레임. |
| `candidates[].captured_at` | 문자열, ISO 8601 `+09:00`, 또는 null | 대표 프레임 번호에 대응하는 ATG 레코드가 없으면 null. ATG 시각을 KST로 해석한다. |
| `candidates[].gps` | GPS 객체 또는 null | 대표 프레임 번호에 대응하는 ATG 레코드가 없으면 null. `captured_at`과 함께 null이 된다. |
| `candidates[].gps.latitude` | 숫자, 위도 십진 도(-90~90) | GPS 객체가 있으면 필수·null 불가. |
| `candidates[].gps.longitude` | 숫자, 경도 십진 도(-180~180) | GPS 객체가 있으면 필수·null 불가. |
| `candidates[].analysis_roi` | ROI 객체, 픽셀 | null 불가. 대표 프레임에 적용한 분석 영역. |
| `candidates[].candidate_bbox` | ROI 객체, 픽셀 | null 불가. 대표 프레임의 후보 사각형. |
| `candidates[].temperature_c` | 온도 객체, °C | null 불가. 후보 사각형 및 주변 배경 온도. |
| `candidates[].roi_temperature_c` | ROI 온도 객체, °C, 또는 null | **필수.** 신규 작업의 성공 결과는 모든 후보에서 객체이며 누락·null이면 성공 처리하지 않는다. 기존 저장 결과에서 계산되지 않은 값만 null 가능. |
| `candidates[].evidence` | 객체 | null 불가. 탐지·트래킹 근거. |
| `candidates[].result_image` | 객체 | null 불가. 후보 PNG 조회 정보. |

### ROI와 후보 사각형 좌표

`processing.analysis_roi`, `candidates[].analysis_roi`, `candidates[].candidate_bbox`는 아래 구조를 공유한다. 상위 객체가 있을 때 네 속성은 모두 필수이며 null 불가다. ROI는 기본적으로 2×3 격자의 하단 가운데 칸이며, 384×288 프레임에서는 `{x:128,y:144,width:128,height:144}`다. 후보 사각형은 해당 분석 ROI 내부의 영역이다.

| 속성 | 타입·단위 | 의미 |
|---|---|---|
| `.x` | 정수, 픽셀 | 원본 프레임 왼쪽부터 시작하는 좌표(0 이상). |
| `.y` | 정수, 픽셀 | 원본 프레임 위쪽부터 시작하는 좌표(0 이상). |
| `.width` | 정수, 픽셀 | 영역의 가로 픽셀 개수(1 이상). |
| `.height` | 정수, 픽셀 | 영역의 세로 픽셀 개수(1 이상). |

### 온도 통계

`temperature_c`의 다섯 속성은 후보 사각형 기준이며 모두 숫자·°C·필수·null 불가다. `roi_temperature_c` 객체가 있으면 그 안의 다섯 속성도 모두 숫자·°C·필수·null 불가다.

| JSON 경로 | 의미 |
|---|---|
| `candidates[].temperature_c.minimum` | 후보 사각형 픽셀 최저 온도. |
| `candidates[].temperature_c.mean` | 후보 사각형 픽셀 평균 온도. |
| `candidates[].temperature_c.maximum` | 후보 사각형 픽셀 최고 온도. |
| `candidates[].temperature_c.background_median` | 후보 사각형 바깥 주변 5픽셀 영역의 온도 중앙값. 주변 픽셀이 없으면 전체 프레임 중앙값. |
| `candidates[].temperature_c.delta_from_background` | 후보 최고 온도 − 주변 배경 중앙값. |
| `candidates[].roi_temperature_c.minimum` | 분석 ROI 전체 픽셀 최저 온도. |
| `candidates[].roi_temperature_c.mean` | 분석 ROI 전체 픽셀 평균 온도. |
| `candidates[].roi_temperature_c.maximum` | 분석 ROI 전체 픽셀 최고 온도. |
| `candidates[].roi_temperature_c.max_minus_min` | ROI 최고 − ROI 최저. |
| `candidates[].roi_temperature_c.max_minus_mean` | ROI 최고 − ROI 평균. |

### 탐지 근거와 이미지

아래 필드는 각 상위 객체가 존재할 때 필수이며 null이 될 수 없다. `evidence.algorithms`는 최소 한 항목이 있고, 나머지 배열은 별도 명시가 없으면 빈 배열일 수 있다.

| JSON 경로 | 타입·단위 | 의미 |
|---|---|---|
| `candidates[].evidence.algorithms` | 문자열 배열 | 해당 후보에 기여한 알고리즘 이름. |
| `candidates[].evidence.max_anomaly_score` | 숫자, 알고리즘 점수 | 대표 프레임 후보의 최대 이상 점수. 온도가 아니다. |
| `candidates[].evidence.mean_anomaly_score` | 숫자, 알고리즘 점수 | 대표 프레임 후보의 평균 이상 점수. 온도가 아니다. |
| `candidates[].evidence.persistence_score` | 숫자, 상대 순위 점수 | 프레임 간 지속성 등에 따른 후보 정렬 점수. |
| `candidates[].evidence.tracked_frame_count` | 정수, 프레임 | 후보가 관찰된 프레임 수. |
| `candidates[].evidence.track_start_frame` | 정수, 0부터 시작하는 프레임 인덱스 | 후보 트랙의 시작 프레임. |
| `candidates[].evidence.track_end_frame` | 정수, 0부터 시작하는 프레임 인덱스 | 후보 트랙의 마지막 프레임(포함). |
| `candidates[].evidence.likely_ground_fixed` | 불리언 | 분석 알고리즘의 지면 고정 추정값이며 관로 이상 확정 판정이 아니다. |
| `candidates[].result_image.format` | 문자열, `PNG` | 결과 이미지 형식. |
| `candidates[].result_image.width` | 정수, 픽셀 | 원본 ATT 프레임과 같은 이미지 가로 크기. |
| `candidates[].result_image.height` | 정수, 픽셀 | 원본 ATT 프레임과 같은 이미지 세로 크기. |
| `candidates[].result_image.path` | 문자열, API 상대 경로 | INT-001 응답에서는 후보 PNG 조회 경로. 배치 산출물의 같은 필드는 내부 저장 경로다. |
| `candidates[].result_image.sha256` | 문자열, 소문자 16진수 64자리 | PNG 파일의 실제 바이트에 대한 SHA-256. null·빈 값 불가. |
| `candidates[].result_image.size_bytes` | 정수, 바이트 | PNG 파일의 실제 크기(1 이상). null 불가. |
| `candidates[].result_image.grid` | 객체 | 이미지에 표시한 격자 정보. |
| `candidates[].result_image.grid.rows` | 정수, 항상 2 | 격자 행 수. |
| `candidates[].result_image.grid.columns` | 정수, 항상 3 | 격자 열 수. |

## 데이터 품질과 사용 조건

- 현재 ATT 프레임과 ATG 레코드는 **같은 0 기반 인덱스**로 결합한다. ATG 레코드가 부족한 프레임의 후보는 `captured_at`·`gps`가 null이고 `warnings`에 `ATT_ATG_COUNT_MISMATCH`가 기록된다. 이 경우 지도에 위치를 임의로 보정해 표시하지 않는다.
- 현재 경고 코드는 `ATT_ATG_COUNT_MISMATCH`(ATT 프레임 수와 ATG 레코드 수 불일치, 예: `ATT frames (3959) and ATG records (3885) differ; unmatched candidate metadata is null.`)와 `EMPTY_THERMAL_FRAMES_SKIPPED`(비어 있거나 유효하지 않은 열화상 프레임 제외, 예: `Skipped 2 empty or invalid thermal frame(s).`)다. 두 경고는 운영·데이터 품질 확인용이며 사용자 화면에 원문을 그대로 표시할 필요는 없다. 위치·촬영 시각이 null인 후보는 포털에서 위치 표시를 생략하거나 확인 필요로 안내한다.
- 현 구현은 ATG 레코드가 존재하면 위도·경도 숫자를 그대로 결과에 넣는다. `gps != null`만으로 유효한 위성 위치가 확인된 것은 아니다. GPS 신호 품질·좌표계·촬영 장비의 시간대는 실제 연동 데이터로 별도 확인해야 한다.
- 후보 ID와 후보 위치는 분석 결과다. 관로 ID 매칭과 최종 이상 판정은 이 결과에 포함되지 않는다.
