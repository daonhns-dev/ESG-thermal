"""Stage/seal an allowlisted r3 candidate without committing or deploying."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
LOCATOR = ROOT / "output" / "analysis-r3-candidate-20261119.json"
OVERRIDES = [
    ".env.example", "compose.portal.yaml", "compose.external-data.yaml", "compose.shared-input.yaml",
    "src/heatpipe_service/api/app.py", "src/heatpipe_service/api/models.py",
    "src/heatpipe_service/jobs/store.py", "src/heatpipe_service/jobs/worker.py",
    "src/heatpipe_service/storage/backends.py", "src/heatpipe_service/storage/shared_inputs.py",
    "tests/unit/test_storage_worker.py", "tests/unit/test_shared_inputs.py",
    "tests/unit/test_request_storage_rollback.py", "tests/fixtures/legacy_v1_request.py",
    "docs/openapi.json", "docs/schemas/analysis-result-v0.schema.json",
    "docs/examples/analysis-result-v0.example.json", "docs/examples/int-003-shared-path.example.json",
    "docs/Q15_SHARED_PATH_V1_1_DRAFT.md", "docs/CLAUDE_STATUS_REVIEW_20261119.md",
]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_bytes(path, content):
    # git archive follows Windows checkout attributes; ship text as LF for Linux.
    if path.suffix in {".py", ".json", ".md", ".yaml", ".yml", ".toml"} or path.name in {".dockerignore", ".env.example", "Dockerfile"}:
        return content.replace(b"\r\n", b"\n")
    return content


def stage():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    parent = ROOT / "tmp" / "analysis-candidates"
    parent.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="r3-20261119-", dir=parent))
    raw = subprocess.check_output(["git", "archive", "--format=tar", commit, "heat-pipe-service"], cwd=REPO)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            if not member.isfile():
                continue
            relative = Path(member.name).relative_to("heat-pipe-service")
            name = relative.as_posix()
            if not (name.startswith("src/") or name.startswith("tests/unit/") or name in
                    {"Dockerfile", "pyproject.toml", "README.md", ".dockerignore", "compose.yaml"}):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_bytes(destination, archive.extractfile(member).read()))
    for name in OVERRIDES:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_bytes(destination, (ROOT / name).read_bytes()))
    algorithm = "src/heatpipe_service/analysis/algorithms.py"
    baseline = subprocess.check_output(["git", "show", f"{commit}:heat-pipe-service/{algorithm}"], cwd=REPO)
    assert (target / algorithm).read_bytes() == baseline
    (target / "CANDIDATE_NOTES.md").write_text("""# r3 API/워커 배포 후보 - 미배포

이 후보는 기준 커밋에 검토 대상 파일만 반영한 스냅샷입니다. Git 커밋/태그가
새로 생성된 것은 아닙니다. algorithms.py의 작업 트리 포맷 변경과 experiments는
제외했습니다. candidate manifest와 JUnit 검증 결과로 파일·테스트를 식별합니다.

서버에는 현재 구버전 이미지와 외부 analysis bind가 실행 중입니다. 이 소스를
서버에 풀거나 Compose를 실행하지 마세요. 코드 고정 지시·이미지 빌드 검증과
VTI 중단 시간 협의 후 별도 절차로 배포해야 합니다. 실제 .env는 포함하지 않습니다.
서버의 기존 .env와 COMPOSE_FILE은 보존해야 하며, shared-input은 VTI 준비 전에는
활성화하지 않습니다. 결과/보안 계약과 공개 API 주소는 기존대로 유지합니다.
""", encoding="utf-8")
    hashes = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(target.rglob("*")) if p.is_file()}
    manifest = {"base_commit": commit, "status": "LOCAL CANDIDATE ONLY; NOT DEPLOYED",
                "overrides": OVERRIDES, "excluded_worktree_changes": [algorithm, "experiments/"],
                "sha256": hashes}
    write(target / "CANDIDATE_MANIFEST.json", manifest)
    LOCATOR.parent.mkdir(exist_ok=True)
    write(LOCATOR, {"stage": str(target), "junit": str(target.parent / (target.name + "-junit.xml"))})
    print(LOCATOR.read_text(encoding="utf-8"))


def seal():
    location = json.loads(LOCATOR.read_text(encoding="utf-8"))
    target = Path(location["stage"])
    manifest = json.loads((target / "CANDIDATE_MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest, name
    report = ET.parse(location["junit"])
    suites = list(report.getroot().iter("testsuite"))
    assert suites and all(int(s.get("failures", 0)) == 0 and int(s.get("errors", 0)) == 0 for s in suites)
    count = sum(int(s.get("tests", 0)) for s in suites)
    assert count >= 87
    write(target / "VALIDATION.json", {"environment": "Linux Python 3.12", "unit_tests": count,
                                      "failures": 0, "errors": 0, "golden_tests": "not run; PyTorch not installed"})
    names = list(manifest["sha256"]) + ["CANDIDATE_MANIFEST.json", "VALIDATION.json"]
    output = ROOT / "output" / "analysis-api-worker-r3-candidate-20261119.tar.gz"
    with tarfile.open(output, "w:gz") as archive:
        for name in names:
            archive.add(target / name, arcname="heat-pipe-service/" + name)
    review = ROOT / "output" / "VTI_API_WORKER_PreDeployReview_20261119.zip"
    with ZipFile(review, "w", ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(target / name, "candidate/" + name)
        for name in ["docs/EXTERNAL_STORAGE_MIGRATION_2026-11-18.md",
                     "docs/CLAUDE_REVIEW_ROLLBACK_STORAGE_R3_20261119.md",
                     "scripts/prepare_analysis_storage_backup.sh", "scripts/backup_verify_analysis_data.sh",
                     "scripts/migrate_analysis_storage.sh", "compose.keep-current-data.yaml"]:
            archive.write(ROOT / name, "operations/" + name)
        archive.writestr("START_HERE.txt", "Read candidate/docs/CLAUDE_STATUS_REVIEW_20261119.md.\n"
                         "Current source candidate is tested, not committed or deployed.\n"
                         "Do not execute operations scripts as part of review.\n")
    with ZipFile(review) as archive:
        assert archive.testzip() is None
        assert not any(Path(n).name == ".env" for n in archive.namelist())
    print(json.dumps({"candidate": str(output), "candidate_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                      "review": str(review), "unit_tests": count}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seal", action="store_true")
    if parser.parse_args().seal:
        seal()
    else:
        stage()
