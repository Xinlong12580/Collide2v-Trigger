from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from collide2v_trigger.calibration import (
    assess_calibration_quality,
    calibrate_rate_from_threshold_scores,
)


def summary(**overrides):
    result = {
        "target_rate_khz": 40.0,
        "measured_rate_khz": 40.0,
        "passing_events": 100,
        "total_events": 100000,
        "collision_rate_mhz": 40.0,
        "finite_scores": 1000,
        "target_bracketed": True,
        "calibrated_threshold_gev": 40.0,
        "calibration_lower_bound_gev": 0.0,
        "calibration_upper_bound_gev": 1000.0,
        "score_tolerance_gev": 0.25,
        "bisection_tolerance_gev": 0.25,
        "right_censored_at_upper_bound": 0,
    }
    result.update(overrides)
    return result


class CalibrationQualityTests(unittest.TestCase):
    def test_well_populated_rate_match_has_no_flags(self):
        quality = assess_calibration_quality(summary())
        self.assertEqual(quality["status"], "ok")
        self.assertEqual(quality["flags"], [])
        self.assertTrue(quality["within_rate_tolerance"])
        self.assertEqual(quality["effective_rate_tolerance_khz"], 4.0)
        self.assertAlmostEqual(quality["naive_relative_statistical_uncertainty"], 0.1)

    def test_zero_target_without_candidates_is_undetermined(self):
        quality = assess_calibration_quality(summary(
            target_rate_khz=0.0, measured_rate_khz=0.0, passing_events=0,
            finite_scores=0, calibrated_threshold_gev=0.0,
        ))
        self.assertTrue(quality["within_rate_tolerance"])
        self.assertIn("zero_target", quality["flags"])
        self.assertIn("threshold_undetermined", quality["flags"])
        self.assertIn("no_events_at_lower_bound", quality["flags"])
        self.assertIsNone(quality["relative_rate_difference"])

    def test_zero_target_with_a_measured_killing_threshold_is_identifiable(self):
        quality = assess_calibration_quality(summary(
            target_rate_khz=0.0, measured_rate_khz=0.0, passing_events=0,
        ))
        self.assertEqual(quality["flags"], ["zero_target"])

    def test_positive_target_zero_acceptance_boundary_is_flagged(self):
        quality = assess_calibration_quality(summary(
            target_rate_khz=1.0, measured_rate_khz=0.0, passing_events=0,
            finite_scores=14, calibrated_threshold_gev=0.244140625,
        ))
        self.assertEqual(quality["status"], "needs_review")
        for flag in ("zero_acceptance", "rate_mismatch", "low_statistics", "near_lower_bound"):
            self.assertIn(flag, quality["flags"])
        self.assertFalse(quality["within_rate_tolerance"])
        self.assertEqual(quality["rate_difference_khz"], -1.0)

    def test_exact_rate_match_can_still_have_low_statistics(self):
        quality = assess_calibration_quality(summary(
            target_rate_khz=2.0, measured_rate_khz=2.0, passing_events=5,
        ))
        self.assertEqual(quality["flags"], ["low_statistics"])
        self.assertTrue(quality["within_rate_tolerance"])
        self.assertAlmostEqual(quality["naive_relative_statistical_uncertainty"], 1 / 5**0.5)

    def test_reporting_tolerances_do_not_imply_statistical_validation(self):
        row = summary(target_rate_khz=2.0, measured_rate_khz=1.6, passing_events=4)
        default = assess_calibration_quality(row)
        self.assertTrue(default["within_rate_tolerance"])
        strict = assess_calibration_quality(
            row, rate_tolerance_relative=0.05, rate_tolerance_khz=0.01,
            min_passing_events=0,
        )
        self.assertFalse(strict["within_rate_tolerance"])
        self.assertIn("rate_mismatch", strict["flags"])
        self.assertNotIn("low_statistics", strict["flags"])

    def test_unbracketed_and_censored_upper_boundary(self):
        quality = assess_calibration_quality(summary(
            target_bracketed=False, calibrated_threshold_gev=1000.0,
            right_censored_at_upper_bound=100,
        ))
        for flag in ("target_not_bracketed", "near_upper_bound", "right_censored_scores"):
            self.assertIn(flag, quality["flags"])

    def test_invalid_reporting_options_fail_before_reading_scores(self):
        for options in (
            {"rate_tolerance_relative": -1},
            {"rate_tolerance_relative": float("nan")},
            {"rate_tolerance_khz": float("inf")},
            {"min_passing_events": -1},
            {"min_passing_events": 0.5},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                calibrate_rate_from_threshold_scores("/does/not/exist", **options)

    def test_quality_is_saved_without_changing_the_search_result(self):
        meta = {
            b"collide2v_trigger.format": b"event-threshold-sidecar-v1",
            b"collide2v_trigger.source": b"toy-source.parquet",
            b"collide2v_trigger.trigger": b"SingleTkMuon_22",
            b"collide2v_trigger.threshold_profile": b"default",
            b"collide2v_trigger.reference_field": b"muon_pt",
            b"collide2v_trigger.scale_fields": b'["muon_pt"]',
            b"collide2v_trigger.base_thresholds_gev": b'{"muon_pt":22.0}',
            b"collide2v_trigger.lower_bound_gev": b"0.0",
            b"collide2v_trigger.upper_bound_gev": b"1000.0",
            b"collide2v_trigger.tolerance_gev": b"0.25",
        }
        schema = pa.schema([pa.field("pt_threshold_gev", pa.float64())], metadata=meta)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pq.write_table(pa.Table.from_arrays([pa.array([0.0] * 14 + [None] * 99986)], schema=schema), root / "scores.parquet")
            with redirect_stdout(io.StringIO()):
                first = calibrate_rate_from_threshold_scores(
                    root, target_rate_khz=1.0, scan_points_file=root / "points.txt",
                )
                relaxed = calibrate_rate_from_threshold_scores(
                    root, target_rate_khz=1.0, rate_tolerance_khz=2.0,
                    min_passing_events=0, output_file=root / "relaxed.json",
                )
            self.assertEqual(first.calibrated_threshold_gev, 0.244140625)
            self.assertEqual(first.calibrated_threshold_gev, relaxed.calibrated_threshold_gev)
            self.assertEqual(first.passing_events, relaxed.passing_events)
            self.assertIn("rate_mismatch", first.quality["flags"])
            self.assertNotIn("rate_mismatch", relaxed.quality["flags"])
            self.assertIn("zero_acceptance", relaxed.quality["flags"])
            saved = json.loads(first.output_file.read_text())
            self.assertEqual(saved["quality"], first.quality)
            quality_line = next(line for line in (root / "points.txt").read_text().splitlines() if line.startswith("# quality:"))
            self.assertEqual(json.loads(quality_line.split(":", 1)[1]), first.quality)


if __name__ == "__main__":
    unittest.main()
