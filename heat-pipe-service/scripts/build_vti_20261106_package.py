"""Prepare the reviewed INT-001/003/004 documents for the 2026-11-06 handoff."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "VTI_API_2차검토반영_20261106_송부용.zip"

FILES = {
    "Q15_API_명세서_v1.0_2차검토반영본_20261106.pdf": "output/pdf/Q15_API_명세서_v1.0_2차검토반영본_20261106.pdf",
    "Q15_T01_결과_필드_설명서.md": "docs/RESULT_JSON_SCHEMA_V0_FIELD_GUIDE.md",
    "Q15_T01_결과_JSON_Schema_v0.json": "docs/schemas/analysis-result-v0.schema.json",
    "Q15_T02_실제_분석결과_INT-001_예시.json": "docs/examples/int-001-real-result-20261102.json",
    "Q15_T03_OpenAPI_명세.json": "docs/openapi.json",
    "Q15_T04_작업상태_진행률_정책.md": "docs/JOB_STATE_V0.md",
    "Q15_T05_실데이터_검증기록.md": "docs/T05_REAL_DATA_VERIFICATION_FOR_VTI_2026-11-04.md",
    "Q15_T06_연동시험_체크리스트_DaonHNS.md": "docs/VTI_INTEGRATION_TEST_CHECKLIST.md",
    "Q15_R2_12_오류코드_전체목록.md": "docs/VTI_R2_12_ERROR_CODES_2026-11-05.md",
    "Q15_R2_08_후보_PNG_3장.zip": "output/VTI_R2_08_PNG_샘플_20261105.zip",
}

README = """# 2026-11-06 VTI 연동 API 전달 준비 자료

- DaonH&S 담당 범위: INT-001, INT-003, INT-004. INT-002와 INT-005는 VTI 담당입니다.
- T-01~T-06은 2026-11-04 송부 파일을 최신 원본으로 다시 묶은 것입니다.
- T-02 실데이터 예시는 2026-11-02의 기존 점수순 max_results=50 실행 결과입니다. 새로운 구간별 선정 방식의 실데이터 검증 결과가 아니며, 상한 적용 전 전체 후보 수는 알 수 없어 detected_candidate_count가 null입니다.
- 작업당 반환 상한은 우선 max_results=50으로 적용합니다. 후보 선정 품질, VTI의 Presigned URL 24시간 발급 설정, 센서 130℃ 상한 여부, ATT·ATG 시각/GPS와 위치 오차, ATT 최대 크기, 시험 서버 주소·접근망은 명세의 후속 확인 항목을 참조하십시오.
- API 요청·응답 형식은 명세 PDF, OpenAPI, 결과 JSON Schema를 함께 확인하십시오.
- 이 패키지에는 Bearer 키 또는 Presigned URL 원문을 포함하지 않습니다. 장비·시험 서버 확인 결과에 따라 후속 확인 항목은 별도로 갱신합니다.
"""


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("README.md", README)
        for archive_name, source_name in FILES.items():
            source = ROOT / source_name
            if not source.is_file():
                raise FileNotFoundError(source)
            archive.write(source, archive_name)
    print(OUTPUT)


if __name__ == "__main__":
    main()
