"""Exercise pre-path parsing/idempotent replay against the new persisted JSON."""
import json
from pathlib import Path
import runpy
import sqlite3

import pytest
from pydantic import ValidationError

from heatpipe_service.api.models import CreateJobRequest
from heatpipe_service.jobs.store import JobStore

LEGACY = runpy.run_path(str(Path(__file__).parents[1] / "fixtures" / "legacy_v1_request.py"))
LegacyRequest = LEGACY["CreateJobRequest"]
LegacyJobStore = LEGACY["LegacyJobStore"]


def request(mode, suffix="old"):
    body = {"monitoring_data_id": "rollback-sample"}
    for name in ("att", "atg"):
        if mode == "url":
            body[name] = {"url": f"https://example.test/{suffix}.{name}"}
        elif mode == "object_key":
            body[name] = {"object_key": f"raw/{suffix}.{name}"}
        else:
            body[name] = {"path": f"2026/11/{suffix}.{name}", "size_bytes": 10, "sha256": "a" * 64}
    return CreateJobRequest.model_validate(body)


def saved(db, job_id):
    with sqlite3.connect(db) as connection:
        return json.loads(connection.execute("SELECT request_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0])


@pytest.mark.parametrize("mode", ["url", "object_key"])
@pytest.mark.parametrize("completed", [False, True])
def test_new_legacy_mode_records_allow_old_parser_and_same_key_replay(tmp_path, mode, completed):
    db = tmp_path / "jobs.sqlite3"
    current = JobStore(db)
    incoming = request(mode)
    job = current.create(incoming, "same-key")
    if completed:
        # Status does not change request serialization; isolate the replay case.
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE jobs SET status='SUCCEEDED', progress_percent=100 WHERE job_id=?", (job.job_id,))
    record = saved(db, job.job_id)
    assert "path" not in record["att"] and "path" not in record["atg"]
    assert "analysis_roi" not in record
    assert "end_exclusive" not in record["frame_range"]
    LegacyRequest.model_validate(record)
    legacy_incoming = LegacyRequest.model_validate(incoming.model_dump(exclude_none=True))
    replay = LegacyJobStore(db).create(legacy_incoming, "same-key")
    assert replay.job_id == job.job_id
    assert current.get_request(job.job_id) == incoming


@pytest.mark.parametrize("mode", ["url", "object_key"])
def test_queued_reference_update_also_excludes_nulls(tmp_path, mode):
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    job = store.create(request(mode), "same-key")
    store.create(request(mode, "new"), "same-key")
    record = saved(db, job.job_id)
    assert "path" not in record["att"]
    assert "path" not in record["atg"]
    LegacyRequest.model_validate(record)
    assert "new" in record["att"][mode]


def test_real_path_remains_persisted_and_is_not_old_model_compatible(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    incoming = request("path")
    job = store.create(incoming, "path-key")
    record = saved(db, job.job_id)
    assert record["att"]["path"] == incoming.att.path
    assert store.get_request(job.job_id) == incoming
    with pytest.raises(ValidationError):
        LegacyRequest.model_validate(record)


def test_existing_null_record_is_readable_but_not_silently_migrated(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    incoming = request("url")
    job = store.create(incoming, "old-key")
    old_json = incoming.model_dump_json()  # Mimic records written before this fix.
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET request_json=?,status='SUCCEEDED' WHERE job_id=?", (old_json, job.job_id))
    assert store.get_request(job.job_id) == incoming
    assert store.create(incoming, "old-key").job_id == job.job_id
    assert saved(db, job.job_id)["att"]["path"] is None
    with pytest.raises(ValidationError):
        LegacyRequest.model_validate(saved(db, job.job_id))


def test_queued_path_to_url_switch_removes_path_field_for_legacy_replay(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    incoming = request("path")
    job = store.create(incoming, "switch-key")
    body = incoming.model_dump(exclude_none=True)
    for name in ("att", "atg"):
        body[name].pop("path")
        body[name]["url"] = f"https://example.test/new.{name}"
    url_request = CreateJobRequest.model_validate(body)
    assert store.create(url_request, "switch-key").job_id == job.job_id
    record = saved(db, job.job_id)
    assert "path" not in record["att"] and "path" not in record["atg"]
    replay = LegacyJobStore(db).create(LegacyRequest.model_validate(body), "switch-key")
    assert replay.job_id == job.job_id


def test_queued_old_null_record_is_normalized_only_when_updated(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    incoming = request("url")
    job = store.create(incoming, "same-key")
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET request_json=? WHERE job_id=?", (incoming.model_dump_json(), job.job_id))
    assert saved(db, job.job_id)["att"]["path"] is None
    store.create(request("url", "refreshed"), "same-key")
    record = saved(db, job.job_id)
    assert "path" not in record["att"]
    LegacyRequest.model_validate(record)
