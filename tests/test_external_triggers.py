from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from collide2v_trigger import ConfigurableCollide2VTriggerMenu, TriggerRegistry, create_trigger_menu
from collide2v_trigger.calibration import (
    build_threshold_scan_plan, calibrate_menu, calibrate_rate_from_threshold_scores,
    discover_threshold_score_files, scan_eos_directory, scan_pt_thresholds,
)


ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / "custom_triggers/diphoton_mass_120_130.py"
NAME = "DoublePhoton_Mass120_130"
FILES = [DEFINITION]


def event(pt, eta=None, phi=None):
    return {"L1T_PhotonTight_PT": pt,
            "L1T_PhotonTight_Eta": [0.0] * len(pt) if eta is None else eta,
            "L1T_PhotonTight_Phi": [0.0, math.pi] if phi is None else phi}


def write_sample(path):
    # Four 125 GeV pairs, with known critical cuts; 7,996 failing events.
    events = [event([low, 125.0**2 / (4 * low)]) for low in [20, 30, 40, 50]]
    events += [event([], phi=[])] * 7996
    pq.write_table(pa.table({key: [row[key] for row in events] for key in events[0]}), path)
    return events


class ExternalTriggerTests(unittest.TestCase):
    def test_menu_interface_and_physics(self):
        menu = create_trigger_menu(custom_trigger_files=FILES)
        self.assertEqual(menu.target_rate_khz(NAME), 10.0)
        self.assertEqual(menu.thresholds_for(NAME), {"photon_pt": 1.0})
        self.assertEqual(len(menu.implementable_triggers), 34)
        self.assertEqual(len(ConfigurableCollide2VTriggerMenu().all_triggers), 42)
        self.assertEqual(len(menu.required_columns_for(NAME)), 3)
        self.assertEqual(menu.menu_summary()[-1]["name"], NAME)
        for pt, expected in [([60, 60], True), ([65, 65], True), ([62.5, 62.5], True),
                             ([59.99, 59.99], False), ([65.01, 65.01], False),
                             ([0.5, 125**2 / 2], False), ([math.nan, 62.5], False)]:
            with self.subTest(pt=pt):
                self.assertEqual(menu.evaluate(NAME, event(pt)), expected)
        self.assertFalse(menu.evaluate(NAME, event([], phi=[])))
        self.assertFalse(menu.evaluate(NAME, event([62.5], phi=[0])))
        self.assertTrue(menu.evaluate(NAME, event([62.5, 100, 62.5], phi=[0, 0.5, math.pi])))
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            menu.evaluate(NAME, event([62.5, 62.5], eta=[]))
        acceptances = [create_trigger_menu(custom_trigger_files=FILES,
                       threshold_overrides={NAME: {"photon_pt": cut}}).evaluate(NAME, event([62.5, 62.5]))
                       for cut in [0, 1, 30, 62.5, 63, 100]]
        self.assertEqual(acceptances, [True, True, True, True, False, False])

    def test_definition_validation_and_monotonic_contract(self):
        definition = TriggerRegistry(FILES).definitions[NAME]
        for changes in [{"reference_field": "missing"}, {"default_thresholds": {"photon_pt": -1}},
                        {"required_columns": "column"}, {"scale_fields": ("missing",)},
                        {"target_rate_khz": math.nan}, {"evaluate": None}]:
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                replace(definition, **changes)
        menu = create_trigger_menu(custom_trigger_files=FILES)
        menu.custom_trigger_definitions[NAME] = replace(definition, monotonic=False)
        with self.assertRaisesRegex(ValueError, "monotonic=True"):
            build_threshold_scan_plan(menu, NAME)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            TriggerRegistry([DEFINITION, DEFINITION])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "collision.py"
            path.write_text(DEFINITION.read_text().replace(NAME, "SingleTkMuon_22"))
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                create_trigger_menu(custom_trigger_files=[path])

    def test_scores_calibration_and_profile_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            source = root / "events.parquet"
            events = write_sample(source)
            scores = root / "scores"
            scores.mkdir()
            summary = scan_pt_thresholds(source, NAME, scores / "events.parquet",
                       custom_trigger_files=FILES, upper_bound_gev=100, tolerance_gev=0.01)
            self.assertEqual(summary.fails_at_lower_bound, 7996)
            table = pq.read_table(summary.output_path)
            self.assertEqual(table.schema.names, ["pt_threshold_gev"])
            for score, expected in zip(table.column(0).to_pylist(), [20, 30, 40, 50]):
                self.assertLessEqual(score, expected)
                self.assertLess(expected - score, 0.01)
            provenance = json.loads(table.schema.metadata[b"collide2v_trigger.trigger_definition"])
            self.assertEqual(provenance["definition"]["fixed_parameters"],
                             {"mass_min_gev": 120.0, "mass_max_gev": 130.0})
            # Calibration must work using metadata alone, without importing the external file.
            with patch("collide2v_trigger.core.external.TriggerRegistry", side_effect=AssertionError("No imports")):
                result = calibrate_rate_from_threshold_scores(scores)
            self.assertEqual(result.target_rate_khz, 10.0)
            self.assertEqual(result.measured_rate_khz, 10.0)
            self.assertEqual(result.passing_events, 2)
            self.assertTrue(30 < result.calibrated_threshold_gev < 30.02)
            menu = create_trigger_menu(custom_trigger_files=FILES,
                       threshold_overrides={NAME: {"photon_pt": result.calibrated_threshold_gev}})
            self.assertEqual(sum(menu.evaluate(NAME, e) for e in events), 2)

    def test_full_pipeline_plots_reload_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            inputs = root / "input"
            inputs.mkdir()
            write_sample(inputs / "events.parquet")
            options = dict(triggers=[NAME], custom_trigger_files=FILES, n_files=1,
                           upper_bound_gev=100, score_tolerance_gev=0.01, progress=False,
                           plot_dpi=60, fail_fast=True)
            result = calibrate_menu(inputs, root / "out", **options)
            self.assertEqual(result.calibrated_triggers, (NAME,))
            menu = create_trigger_menu(custom_trigger_files=FILES,
                                       threshold_config=result.threshold_config_file)
            self.assertTrue(30 < menu.threshold(NAME, "photon_pt") < 30.02)
            self.assertEqual(menu.threshold("SingleTkMuon_22", "muon_pt"), 22)
            with self.assertRaisesRegex(ValueError, "requires external trigger"):
                create_trigger_menu(threshold_config=result.threshold_config_file)
            row = json.loads(result.summary_file.read_text())["triggers"][NAME]
            self.assertGreater(Path(row["plot_file"]).stat().st_size, 1000)
            from collide2v_trigger.plotting import read_threshold_scan
            scan = read_threshold_scan(row["monotonic_points_file"])
            self.assertEqual(scan.metadata["trigger_definition"]["definition"]["target_rate_khz"], 10)
            self.assertEqual(scan.reference_field, "photon_pt")
            with patch("collide2v_trigger.calibration.menu_calibration.scan_pt_thresholds",
                       side_effect=AssertionError("Must reuse scores")):
                resumed = calibrate_menu(inputs, root / "out", **options)
            self.assertFalse(resumed.failed_triggers)

    def test_changed_rule_rejects_scores_profile_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            plugin = root / "trigger.py"
            original = DEFINITION.read_text()
            plugin.write_text(original)
            inputs = root / "input"
            inputs.mkdir()
            write_sample(inputs / "events.parquet")
            options = dict(triggers=[NAME], custom_trigger_files=[plugin], n_files=1,
                           upper_bound_gev=100, make_plots=False, progress=False, fail_fast=True)
            result = calibrate_menu(inputs, root / "out", **options)
            plugin.write_text(original.replace('version="1"', 'version="2"'))
            with self.assertRaisesRegex(ValueError, "definition changed"):
                create_trigger_menu(custom_trigger_files=[plugin], threshold_config=result.threshold_config_file)
            with self.assertRaisesRegex(ValueError, "different.*settings"):
                calibrate_menu(inputs, root / "out", **options)
            old = next((root / "out" / NAME / "scores").glob("*.parquet"))
            table = pq.read_table(old)
            metadata = dict(table.schema.metadata)
            provenance = TriggerRegistry([plugin]).provenance[NAME]
            metadata[b"collide2v_trigger.trigger_definition"] = json.dumps(provenance).encode()
            metadata[b"collide2v_trigger.source"] = b"a-different-source"
            pq.write_table(table.replace_schema_metadata(metadata), old.with_name("changed.parquet"))
            with self.assertRaisesRegex(ValueError, "inconsistent scan configurations"):
                discover_threshold_score_files(old.parent)

    def test_cli_evaluation_and_directory_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "input"
            inputs.mkdir()
            source = inputs / "events.parquet"
            write_sample(source)
            output = root / "bits.parquet"
            command = [sys.executable, "-m", "collide2v_trigger.runners.apply_object",
                       str(source), "--custom-trigger-file", str(DEFINITION),
                       "--trigger", NAME, "-o", str(output)]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            table = pq.read_table(output)
            self.assertEqual(table.schema.names,
                             ["source_row", f"Trigger_{NAME}", "Trigger_passes_any"])
            self.assertEqual(sum(table[f"Trigger_{NAME}"].to_pylist()), 4)
            result = scan_eos_directory(inputs, NAME, root / "scores", n_files=1,
                                       custom_trigger_files=FILES, upper_bound_gev=100)
            self.assertEqual(len(result.completed_scans), 1)
            repeated = scan_eos_directory(inputs, NAME, root / "scores", n_files=1,
                                         custom_trigger_files=FILES, upper_bound_gev=100)
            self.assertEqual(len(repeated.skipped_outputs), 1)
            with self.assertRaisesRegex(ValueError, "different definition or scan settings"):
                scan_eos_directory(inputs, NAME, root / "scores", n_files=1,
                                   custom_trigger_files=FILES, upper_bound_gev=200)

    def test_unchanged_definition_can_move_and_multiple_exports_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            moved = root / "moved.py"
            moved.write_bytes(DEFINITION.read_bytes())
            original = TriggerRegistry(FILES).provenance[NAME]
            profile = root / "thresholds.json"
            profile.write_text(json.dumps({
                "schema_version": 1, "custom_trigger_definitions": {NAME: original},
                "thresholds": {NAME: {"photon_pt": 42.0}},
            }))
            menu = create_trigger_menu(custom_trigger_files=[moved], threshold_config=profile)
            self.assertEqual(menu.threshold(NAME, "photon_pt"), 42)
            multiple = root / "multiple.py"
            multiple.write_text(DEFINITION.read_text() + '\nfrom dataclasses import replace\n'
                                'TRIGGERS = [TRIGGER, replace(TRIGGER, name="AnotherDiphoton")]\ndel TRIGGER\n')
            menu = create_trigger_menu(custom_trigger_files=[multiple])
            self.assertTrue(menu.evaluate("AnotherDiphoton", event([62.5, 62.5])))
            self.assertEqual(len(menu.all_triggers), 44)


if __name__ == "__main__":
    unittest.main()
