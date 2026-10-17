from __future__ import annotations

import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from collide2v_trigger import ConfigurableCollide2VTriggerMenu, IDBasedTriggerMenu
from collide2v_trigger.calibration import (
    build_threshold_scan_plan,
    calibrate_rate_from_threshold_scores,
    list_parquet_files,
    scan_eos_directory,
    scan_pt_thresholds,
    select_parquet_files,
)
from collide2v_trigger.io import open_parquet_file, source_filename, source_stem
from collide2v_trigger.runners.common import (
    compact_output_schema,
    evaluate_compact_batch,
    resolve_output_columns,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class TriggerCoreTests(unittest.TestCase):
    def test_threshold_profile_and_constructor_precedence(self) -> None:
        menu = ConfigurableCollide2VTriggerMenu(
            threshold_config=REPOSITORY_ROOT / "configs/toy_threshold_overrides.json",
            threshold_overrides={"SingleTkMuon_22": {"muon_pt": 13.5}},
        )

        self.assertEqual(menu.threshold("SingleTkMuon_22", "muon_pt"), 13.5)
        self.assertEqual(menu.threshold("SingleTkElectron_36", "electron_pt"), 9.0)
        self.assertEqual(
            menu.threshold("DoubleTkElectron_25_12", "leading_electron_pt"),
            25.0,
        )
        self.assertEqual(menu.target_rate_khz("SingleTkMuon_22"), 17.0)
        self.assertEqual(len(menu.all_triggers), 42)
        self.assertEqual(len(menu.implementable_triggers), 33)

    def test_id_menu_exposes_runner_conventions(self) -> None:
        menu = IDBasedTriggerMenu()
        self.assertNotIn("L1T_ScalarHT_HT", menu.required_columns)
        self.assertEqual(len(menu.implementable_triggers), 33)


class ParquetInputTests(unittest.TestCase):
    def test_local_and_simulated_root_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            local = Path(temporary_directory) / "events.parquet"
            pq.write_table(pa.table({"event": [1, 2, 3]}), local)

            with open_parquet_file(local) as parquet_file:
                self.assertEqual(parquet_file.metadata.num_rows, 3)

            remote = "root://test.example//eos/events.parquet"
            opened = []

            class FakeFilesystem:
                def open(self, path: str, mode: str):
                    if path != "//eos/events.parquet":
                        raise AssertionError(f"Unexpected filesystem path: {path}")
                    handle = local.open(mode)
                    opened.append(handle)
                    return handle

            fake_fsspec = types.ModuleType("fsspec")
            fake_fsspec.core = types.SimpleNamespace(
                url_to_fs=lambda url: (FakeFilesystem(), "//eos/events.parquet")
            )
            with patch.dict(sys.modules, {"fsspec": fake_fsspec}):
                with open_parquet_file(remote) as parquet_file:
                    self.assertEqual(parquet_file.metadata.num_rows, 3)

            self.assertTrue(opened[0].closed)
            self.assertEqual(source_filename(remote), "events.parquet")
            self.assertEqual(source_stem(remote), "events")


class CompactOutputTests(unittest.TestCase):
    def test_only_provenance_requested_columns_and_bits_are_saved(self) -> None:
        batch = pa.record_batch(
            {
                "Event_Number": [[101], [102]],
                "Event_Weight": [[1.0], [2.0]],
                "physics_value": [[-1.0], [3.0]],
            }
        )

        class Menu:
            required_columns = ["physics_value"]

            @staticmethod
            def evaluate(name, event):
                return event["physics_value"][0] > 0

        kept = resolve_output_columns(batch.schema, ["Event_Weight"])
        schema = compact_output_schema(
            batch.schema,
            ["ToySeed"],
            kept,
            source="root://host//eos/events.parquet",
            menu_name="test",
        )
        result = evaluate_compact_batch(
            batch,
            Menu(),
            ["ToySeed"],
            kept,
            schema,
            source_row_offset=10,
        )

        self.assertEqual(
            result.schema.names,
            [
                "source_row",
                "Event_Number",
                "Event_Weight",
                "Trigger_ToySeed",
                "Trigger_passes_any",
            ],
        )
        self.assertEqual(result.column(0).to_pylist(), [10, 11])
        self.assertEqual(result.column(3).to_pylist(), [False, True])
        self.assertNotIn("physics_value", result.schema.names)


class ThresholdScanTests(unittest.TestCase):
    def test_multileg_plan_preserves_default_ratios(self) -> None:
        menu = ConfigurableCollide2VTriggerMenu()
        plan = build_threshold_scan_plan(menu, "DoubleTkMuon_15_7")

        self.assertEqual(plan.reference_field, "leading_muon_pt")
        self.assertEqual(
            plan.thresholds_at(30.0),
            {"leading_muon_pt": 30.0, "subleading_muon_pt": 14.0},
        )

    def test_scan_writes_only_one_threshold_column(self) -> None:
        menu = ConfigurableCollide2VTriggerMenu()
        event_count = 3
        columns = {
            name: [[] for _ in range(event_count)] for name in menu.required_columns
        }
        columns["L1T_MuonTight_PT"] = [[50.0], [125.0], []]
        columns["L1T_MuonTight_Eta"] = [[0.1], [-0.2], []]
        columns["L1T_MuonTight_Phi"] = [[0.0], [1.0], []]
        columns["L1T_MuonTight_Charge"] = [[1], [-1], []]
        columns["L1T_MuonTight_DZ"] = [[0.0], [0.0], []]

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "events.parquet"
            output_path = root / "thresholds.parquet"
            pq.write_table(pa.table(columns), input_path)

            summary = scan_pt_thresholds(
                input_path,
                "SingleTkMuon_22",
                output_path,
                upper_bound_gev=1000.0,
                tolerance_gev=0.01,
                batch_size=2,
            )
            result = pq.read_table(output_path)

        self.assertEqual(result.schema.names, ["pt_threshold_gev"])
        thresholds = result.column(0).to_pylist()
        self.assertLessEqual(thresholds[0], 50.0)
        self.assertLess(50.0 - thresholds[0], 0.01)
        self.assertLessEqual(thresholds[1], 125.0)
        self.assertLess(125.0 - thresholds[1], 0.01)
        self.assertTrue(math.isnan(thresholds[2]))
        self.assertEqual(summary.event_count, 3)
        self.assertEqual(summary.fails_at_lower_bound, 1)
        self.assertEqual(summary.passes_at_upper_bound, 0)

    def test_directory_wrapper_selects_files_and_skips_existing_outputs(self) -> None:
        menu = ConfigurableCollide2VTriggerMenu()
        columns = {name: [[0.0]] for name in menu.required_columns}
        columns["L1T_MuonTight_PT"] = [[50.0]]
        columns["L1T_MuonTight_Eta"] = [[0.1]]

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inputs = root / "inputs"
            outputs = root / "outputs"
            inputs.mkdir()
            for index in range(3):
                pq.write_table(
                    pa.table(columns),
                    inputs / f"minbias-{index:02d}.parquet",
                )
            (inputs / ".sys.v#.minbias-99.parquet").write_bytes(b"not parquet")
            (inputs / "notes.txt").write_text("ignored", encoding="utf-8")

            available = list_parquet_files(inputs)
            self.assertEqual(len(available), 3)
            self.assertEqual(
                select_parquet_files(available, n_files=2, selection="first"),
                available[:2],
            )
            random_two = select_parquet_files(
                available, n_files=2, selection="random", random_seed=7
            )
            random_three = select_parquet_files(
                available, n_files=3, selection="random", random_seed=7
            )
            self.assertEqual(random_two, random_three[:2])

            first = scan_eos_directory(
                inputs,
                "SingleTkMuon_22",
                outputs,
                n_files=2,
                selection="first",
                tolerance_gev=0.25,
            )
            second = scan_eos_directory(
                inputs,
                "SingleTkMuon_22",
                outputs,
                n_files=2,
                selection="first",
                tolerance_gev=0.25,
            )

            self.assertEqual(len(first.completed_scans), 2)
            self.assertEqual(len(first.skipped_outputs), 0)
            self.assertEqual(len(second.completed_scans), 0)
            self.assertEqual(len(second.skipped_outputs), 2)
            for output in outputs.glob("*.parquet"):
                self.assertEqual(pq.read_schema(output).names, ["pt_threshold_gev"])

    def test_eos_listing_joins_bare_xrootd_filenames(self) -> None:
        class FakeFilesystem:
            @staticmethod
            def ls(path: str, detail: bool):
                self.assertEqual(path, "//eos/sample")
                self.assertFalse(detail)
                return [
                    "minbias-02.parquet",
                    ".sys.v#.minbias-01.parquet",
                    "notes.txt",
                    "minbias-01.parquet",
                ]

        fake_fsspec = types.ModuleType("fsspec")
        fake_fsspec.core = types.SimpleNamespace(
            url_to_fs=lambda url: (FakeFilesystem(), "//eos/sample")
        )
        with patch.dict(sys.modules, {"fsspec": fake_fsspec}):
            files = list_parquet_files("root://test.example//eos/sample")

        self.assertEqual(
            files,
            [
                "root://test.example//eos/sample/minbias-01.parquet",
                "root://test.example//eos/sample/minbias-02.parquet",
            ],
        )

    def test_rate_calibration_parses_trigger_and_saves_scan_points(self) -> None:
        metadata = {
            b"collide2v_trigger.format": b"event-threshold-sidecar-v1",
            b"collide2v_trigger.source": b"root://host//eos/events.parquet",
            b"collide2v_trigger.trigger": b"SingleTkMuon_22",
            b"collide2v_trigger.threshold_profile": b"default",
            b"collide2v_trigger.reference_field": b"muon_pt",
            b"collide2v_trigger.scale_fields": b'["muon_pt"]',
            b"collide2v_trigger.base_thresholds_gev": b'{"muon_pt": 22.0}',
            b"collide2v_trigger.lower_bound_gev": b"0.0",
            b"collide2v_trigger.upper_bound_gev": b"200.0",
            b"collide2v_trigger.tolerance_gev": b"0.25",
        }
        schema = pa.schema(
            [pa.field("pt_threshold_gev", pa.float64())],
            metadata=metadata,
        )
        table = pa.Table.from_arrays(
            [pa.array([100.0, 50.0, math.nan, 25.0])],
            schema=schema,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pq.write_table(table, root / "scores.parquet")
            pq.write_table(pa.table({"unrelated": [1]}), root / "events.parquet")

            scan_points = root / "monotonic_points.txt"
            monotonic = calibrate_rate_from_threshold_scores(
                root,
                target_rate_khz=10_000.0,
                method="monotonic",
                spacing_gev=25.0,
                output_file=root / "monotonic.json",
                scan_points_file=scan_points,
            )
            bisection = calibrate_rate_from_threshold_scores(
                root,
                target_rate_khz=10_000.0,
                method="bisection",
                tolerance_gev=0.25,
                output_file=root / "bisection.json",
            )
            menu_default = calibrate_rate_from_threshold_scores(
                root,
                method="bisection",
                output_file=root / "menu_default.json",
            )

            summary = json.loads((root / "monotonic.json").read_text())
            point_lines = [
                line
                for line in scan_points.read_text().splitlines()
                if line and not line.startswith("#")
            ]
            point_text = scan_points.read_text()

        self.assertEqual(monotonic.trigger_name, "SingleTkMuon_22")
        self.assertEqual(monotonic.calibrated_threshold_gev, 75.0)
        self.assertEqual(monotonic.measured_rate_khz, 10_000.0)
        self.assertTrue(monotonic.target_bracketed)
        self.assertEqual(summary["reference_field"], "muon_pt")
        self.assertIn("# format: rate-scan-points-v2", point_text)
        self.assertIn("# reference_field: muon_pt", point_text)
        self.assertIn("# collision_rate_mhz: 40", point_text)
        self.assertIn("# base_thresholds_gev: {\"muon_pt\":22.0}", point_text)
        self.assertEqual(len(point_lines), 10)  # header plus 0..200 in 25 GeV steps
        self.assertGreater(bisection.calibrated_threshold_gev, 50.0)
        self.assertLessEqual(bisection.calibrated_threshold_gev, 50.25)
        self.assertEqual(bisection.measured_rate_khz, 10_000.0)
        self.assertEqual(menu_default.target_rate_khz, 17.0)


if __name__ == "__main__":
    unittest.main()
