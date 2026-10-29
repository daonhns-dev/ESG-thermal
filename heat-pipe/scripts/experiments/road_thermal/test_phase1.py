"""Focused tests for the first road-thermal experiment scripts."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from scripts.experiments.road_thermal.session_qc import inspect_session
from scripts.experiments.road_thermal.sync_audit import audit, read_events, summarize_session


def event(event_id: str, rgb_idx: int, offset: int) -> dict:
    return {
        "event_id": event_id,
        "rgb_idx": rgb_idx,
        "thermal_idx": rgb_idx + offset,
        "offset_frames": offset,
        "uncertainty_frames": 0.0,
        "description": "shared road-plane event",
    }


class SessionQcTests(unittest.TestCase):
    def test_reports_missing_avi_and_zero_frame_without_inventing_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            att = Path(directory) / "20260813_000000.att"
            atg = att.with_suffix(".atg")
            att.write_bytes(struct.pack("<4H", 1, 0, 2, 2) +
                            struct.pack("<4H", 3000, 3100, 3200, 3300) +
                            struct.pack("<4H", 0, 0, 0, 0))
            records = []
            for second in (0, 1):
                timestamp = f"2026-08-13 00:00:0{second}".encode("ascii")
                records.append(timestamp + struct.pack("<idd", 14, 37.5, 126.8))
            atg.write_bytes(b"".join(records))

            report = inspect_session(att, zero_sample_stride=1)
            self.assertEqual(report["thermal_frames"], 2)
            self.assertEqual(report["atg_records"], 2)
            self.assertEqual(report["elapsed_seconds"], 1)
            self.assertEqual(report["sampled_zero_frames"], [1])
            self.assertTrue(report["zero_scan_complete"])
            self.assertIn("avi_missing", report["issues"])


class SyncAuditTests(unittest.TestCase):
    info = {"session": "test", "avi_metadata_frames": 300, "issues": []}

    def test_constant_offset(self) -> None:
        events = [event("a", 10, 2), event("b", 110, 2), event("c", 210, 2)]
        result = summarize_session(self.info, events)
        self.assertEqual(result["classification"], "constant_offset_compatible")
        self.assertEqual(result["compatible_constant_offset_interval_frames"], [2, 2])

    def test_linear_drift(self) -> None:
        events = [event("a", 10, 0), event("b", 110, 2), event("c", 210, 4)]
        result = summarize_session(self.info, events)
        self.assertEqual(result["classification"], "linear_drift_compatible")

    def test_inconsistent_offsets(self) -> None:
        events = [event("a", 10, 0), event("b", 110, 4), event("c", 210, 1)]
        result = summarize_session(self.info, events)
        self.assertEqual(result["classification"], "inconsistent_offsets")

    def test_missing_late_phase_is_unconfirmed(self) -> None:
        events = [event("a", 10, 2), event("b", 110, 2), event("c", 150, 2)]
        result = summarize_session(self.info, events)
        self.assertEqual(result["classification"], "unconfirmed_insufficient_coverage")

    def test_event_csv_checks_frame_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            path.write_text(
                "session,event_id,rgb_idx,thermal_idx,rgb_uncertainty_frames,"
                "thermal_uncertainty_frames,description\n"
                "test,one,10,12,1,1,shared physical event\n",
                encoding="utf-8",
            )
            info = {**self.info, "thermal_frames": 300}
            rows = read_events(path, {"test": info})
            self.assertEqual(rows["test"][0]["offset_frames"], 2)
            path.write_text(
                path.read_text(encoding="utf-8").replace(",10,12,", ",300,12,"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exceeds"):
                read_events(path, {"test": info})

    def test_event_csv_rejects_missing_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            path.write_text(
                "session,event_id,rgb_idx,thermal_idx,rgb_uncertainty_frames,"
                "thermal_uncertainty_frames,description\n"
                "test,one,10,12,1,1\n",
                encoding="utf-8",
            )
            info = {**self.info, "thermal_frames": 300}
            with self.assertRaisesRegex(ValueError, "missing values"):
                read_events(path, {"test": info})

    def test_audit_keeps_unannotated_sessions_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            path.write_text(
                "session,event_id,rgb_idx,thermal_idx,rgb_uncertainty_frames,"
                "thermal_uncertainty_frames,description\n",
                encoding="utf-8",
            )
            manifest = {"schema_version": 1, "sessions": [self.info]}
            report = audit(manifest, path)
            self.assertEqual(
                report["sessions"][0]["classification"],
                "unconfirmed_insufficient_coverage",
            )


if __name__ == "__main__":
    unittest.main()
