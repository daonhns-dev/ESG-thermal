"""Build the document-only v1.1 handoff; no server operations or secrets."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

import build_q15_pdf as pdf
import build_shared_path_example as synthetic

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "output" / "VTI_API_v1.1_PLAN_A_20261119"
PDF_NAME = "Q15_API_변경명세_v1.1_방안A_20261119.pdf"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    STAGE.mkdir(parents=True, exist_ok=True)
    synthetic.main()
    md = ROOT / "docs" / "Q15_SHARED_PATH_V1_1_SEND_20261119.md"
    pdf.SOURCE = md
    pdf.OUTPUT = ROOT / "output" / "pdf" / PDF_NAME
    pdf.DOCUMENT_TITLE = "Q15 연동 API 변경 명세 - 방안 A"
    pdf.DOCUMENT_VERSION = "v1.1"
    pdf.DOCUMENT_DATE = "2026-11-19"
    pdf.main()
    shutil.copy2(pdf.OUTPUT, STAGE / PDF_NAME)
    shutil.copy2(md, STAGE / "Q15_API_PLAN_A_v1.1_20261119.md")
    contract = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    contract["info"].update({"x-contract-version": "v1.1", "x-issued-on": "2026-11-19",
                             "x-deployment-status": "not-applied; development handoff"})
    dump(STAGE / "openapi-v1.1.json", contract)
    request = json.loads((ROOT / "docs" / "examples" / "int-003-shared-path.example.json").read_text(encoding="utf-8"))
    dump(STAGE / "examples" / "int-003-path.request.json", request)
    url_request = deepcopy(request)
    for name in ("att", "atg"):
        relative = url_request[name].pop("path")
        url_request[name]["url"] = "http://portal-minio:9000/heatpipe-portal/raw/" + relative + "?X-Amz-Signature=EXAMPLE_NOT_VALID"
    dump(STAGE / "examples" / "int-003-url.request.json", url_request)
    mixed = deepcopy(request)
    mixed["atg"] = url_request["atg"]
    dump(STAGE / "examples" / "int-003-mixed.request.json", mixed)
    dump(STAGE / "examples" / "int-003-202.response.json", {
        "job_id": "JOB-20261119-000001", "monitoring_data_id": request["monitoring_data_id"],
        "status": "QUEUED", "created_at": "2026-11-19T00:00:00Z"})
    dump(STAGE / "examples" / "int-003-503.response.json", {
        "error": {"code": "SHARED_INPUT_NOT_CONFIGURED", "message": "Shared input storage is not ready"}})
    for name in ("att", "atg"):
        relative = Path(request[name]["path"])
        original = ROOT / "tmp" / "shared-path-example" / relative
        assert original.stat().st_size == request[name]["size_bytes"]
        assert hashlib.sha256(original.read_bytes()).hexdigest() == request[name]["sha256"]
        output = STAGE / "samples" / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, output)
    error_codes = (ROOT / "docs" / "VTI_R2_12_ERROR_CODES_2026-11-05.md").read_text(encoding="utf-8")
    error_codes = error_codes.replace("# R2-12 오류 응답 형식과 코드 목록", "# Q15 v1.1 오류 응답 형식과 코드 목록\n\n제공일: 2026-11-19. 개발 착수용. 새 경로 계약은 서버 미적용.")
    error_codes = error_codes.replace("2026-11-18 방안 A 검토본 추가:", "v1.1 추가:").replace("방안 A 검토본 추가:", "v1.1 추가:")
    (STAGE / "ERROR_CODES.md").write_text(error_codes, encoding="utf-8")
    shutil.copy2(ROOT / "docs" / "schemas" / "analysis-result-v0.schema.json",
                 STAGE / "analysis-result-v0.schema.json")
    (STAGE / "README.md").write_text("""# Q15 v1.1 방안 A 변경 명세 - 2026-11-19 송부용

본 자료는 VTI 포털 개발 착수용 명세입니다. 새 path 방식은 시험 서버 미적용이며,
양사 배포·데이터 이전·통합 시험 후 전환 완료를 별도 안내합니다.

- PDF/Markdown: 기존 Q15 v1.0 대비 변경 계약과 배포 전제. 변경은 본 자료가 우선합니다.
- openapi-v1.1.json: 전체 API 계약. info.version은 서비스 버전,
  info.x-contract-version은 계약 버전(v1.1)입니다.
- examples/: 경로·URL 예비·혼합 요청, 202/503 응답 예시. job_id·시각·서명은 가상 값입니다.
- samples/: 실 촬영 자료가 아닌 4프레임 소형 합성 ATT·ATG입니다.
  samples/ 하위 구조를 공유 원본 루트에 게시하면 path 예시와 크기·해시가 일치합니다.
  서버 게시와 분석 요청은 전환 준비 완료 안내 후 실시해 주세요.
- ERROR_CODES.md: 기존 오류 전체 목록과 v1.1 추가 오류.
- analysis-result-v0.schema.json: 변경하지 않은 INT-001 결과 스키마.

경로 입력은 파일별 path, size_bytes, sha256을 사용하고 원본은 읽기 전용입니다.
URL은 자동 fallback이 아니라 요청에서 선택하는 예비 방식입니다.
공유 루트 표식·포털 원본 공개 절차·마운트 기동 순서가 준비돼야 합니다.
일반 원본 조회·재분석·보관/삭제 기능은 VTI가 기존 흐름을 유지하도록 전환합니다.

소스 코드, Compose 적용 파일, 비밀키, .env, 실제 촬영 원본은 이 자료에 포함하지 않습니다.
이 ZIP은 API 명세 전달용이며 설치/배포 패키지가 아닙니다.
""", encoding="utf-8")
    # Explicit allowlist avoids carrying stale or unrelated files from a previous build.
    names = [PDF_NAME, "Q15_API_PLAN_A_v1.1_20261119.md", "openapi-v1.1.json",
             "ERROR_CODES.md", "analysis-result-v0.schema.json", "README.md"]
    names += ["examples/" + name for name in ("int-003-path.request.json", "int-003-url.request.json",
              "int-003-mixed.request.json", "int-003-202.response.json", "int-003-503.response.json")]
    names += ["samples/" + request[name]["path"] for name in ("att", "atg")]
    manifest = {name: hashlib.sha256((STAGE / name).read_bytes()).hexdigest() for name in names}
    dump(STAGE / "SHA256SUMS.json", manifest)
    archive = ROOT / "output" / "VTI_API_v1.1_방안A_20261119_송부용.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zipfile:
        for name in names + ["SHA256SUMS.json"]:
            zipfile.write(STAGE / name, name)
    with ZipFile(archive) as zipfile:
        assert zipfile.testzip() is None
        assert len(zipfile.namelist()) == len(names) + 1
    print(archive)


if __name__ == "__main__":
    main()
