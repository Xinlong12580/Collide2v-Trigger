from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
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

from collide2v_trigger.calibration import calibrate_menu, scan_pt_thresholds
from collide2v_trigger.core.configurable_menu import ConfigurableCollide2VTriggerMenu


MODULE = "collide2v_trigger.calibration.menu_calibration"


def write_events(path: Path, *, empty_muons: bool = False) -> None:
    menu = ConfigurableCollide2VTriggerMenu()
    columns = {name: [[] for _ in range(4)] for name in menu.required_columns}
    muon_pt = [[50.0, 20.0], [30.0, 18.0], [], [100.0, 40.0]]
    if empty_muons:
        muon_pt = [[] for _ in range(4)]
    for field in ("PT", "Eta", "Phi", "Charge", "DZ"):
        columns[f"L1T_MuonTight_{field}"] = [
            values if field == "PT" else [0.0] * len(values)
            for values in muon_pt
        ]
    jet_pt = [[100.0, 70.0], [40.0, 30.0], [], [60.0, 40.0]]
    for field in ("PT", "Eta", "Phi", "Mass"):
        columns[f"{menu.PUPPI_JET}_{field}"] = [
            values if field == "PT" else [0.0] * len(values)
            for values in jet_pt
        ]
    pq.write_table(pa.table(columns), path)


class MenuCalibrationTests(unittest.TestCase):
    def test_pipeline_exports_multileg_and_fixed_thresholds_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            for index in range(3):
                write_events(inputs / f"minbias-{index}.parquet")
            output = root / "output"
            base_config = root / "base_thresholds.json"
            base_config.write_text(json.dumps({
                "schema_version": 1,
                "profile": "test_base",
                "thresholds": {
                    "DoubleTkMuon_15_7": {"leading_muon_pt": 20.0, "subleading_muon_pt": 9.0},
                    "PuppiHT_450": {"ht_jet_pt_min": 35.0},
                    "SingleTkMuon_22": {"muon_pt": 13.0},
                },
            }))
            names = ["DoubleTkMuon_15_7", "PuppiHT_450", "SinglePuppiJet_230"]
            options = dict(
                triggers=names, n_files=2, random_seed=17,
                upper_bound_gev=200.0, score_tolerance_gev=0.1,
                spacing_gev=25.0,
                target_rates_khz={name: 10_000.0 for name in names},
                threshold_config=base_config,
                make_plots=importlib.util.find_spec("matplotlib") is not None,
                progress=False,
            )
            with redirect_stdout(io.StringIO()):
                result = calibrate_menu(inputs, output, **options)
            config = json.loads(result.threshold_config_file.read_text())
            summary = json.loads(result.summary_file.read_text())
            self.assertEqual(set(result.calibrated_triggers), set(names))
            self.assertFalse(result.failed_triggers)
            self.assertFalse(result.unreachable_triggers)
            self.assertEqual(len(result.selected_files), 2)
            loaded_menu = ConfigurableCollide2VTriggerMenu(
                threshold_config=result.threshold_config_file
            )
            fields = loaded_menu.thresholds_for("DoubleTkMuon_15_7")
            self.assertAlmostEqual(fields["subleading_muon_pt"] / fields["leading_muon_pt"], 9.0 / 20.0)
            self.assertEqual(loaded_menu.threshold("PuppiHT_450", "ht_jet_pt_min"), 35.0)
            self.assertEqual(loaded_menu.threshold("SingleTkMuon_22", "muon_pt"), 13.0)
            self.assertTrue(config["calibration_complete"])
            self.assertTrue(config["quality_review_required"])
            self.assertEqual(set(config["flagged_triggers"]), set(names))
            for name in names:
                row = summary["triggers"][name]
                self.assertEqual(row["total_events"], 8)
                self.assertEqual(row["measured_rate_khz"], 10_000.0)
                self.assertIn("low_statistics", row["quality"]["flags"])
                self.assertEqual(config["calibration_quality"][name], row["quality"])
                score_sources = set()
                for score in (output / name / "scores").glob("*.parquet"):
                    schema = pq.read_schema(score)
                    self.assertEqual(schema.names, ["pt_threshold_gev"])
                    score_sources.add(schema.metadata[b"collide2v_trigger.source"].decode())
                self.assertEqual(score_sources, set(result.selected_files))
                if options["make_plots"]:
                    from collide2v_trigger.plotting import read_threshold_scan

                    self.assertGreater(Path(row["plot_file"]).stat().st_size, 1000)
                    scan = read_threshold_scan(
                        row["monotonic_points_file"],
                        calibration_file=row["monotonic_file"],
                        selected_calibration_file=row["bisection_file"],
                    )
                    self.assertEqual(scan.selected_threshold_gev, row["calibrated_threshold_gev"])
                    self.assertEqual(scan.metadata["selected_method"], "bisection")
                    self.assertEqual(scan.metadata["quality"], row["quality"])

            with patch(f"{MODULE}.scan_pt_thresholds", side_effect=AssertionError("Should reuse scores")), \
                 patch(f"{MODULE}.list_parquet_files", side_effect=AssertionError("Should reuse manifest")), \
                 redirect_stdout(io.StringIO()):
                resumed = calibrate_menu(inputs, output, **options)
            self.assertFalse(resumed.failed_triggers)
            resumed_summary = json.loads(resumed.summary_file.read_text())
            self.assertTrue(all(row["reused_score_files"] == 2 for row in resumed_summary["triggers"].values()))
            with self.assertRaisesRegex(ValueError, "different.*settings"):
                calibrate_menu(inputs, output, **{**options, "score_tolerance_gev": 0.2})

    def test_unreachable_target_keeps_base_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            write_events(inputs / "minbias.parquet", empty_muons=True)
            with redirect_stdout(io.StringIO()):
                result = calibrate_menu(
                    inputs, root / "output", triggers=["SingleTkMuon_22"],
                    n_files=1, make_plots=False, progress=False,
                )
            self.assertEqual(result.unreachable_triggers, ("SingleTkMuon_22",))
            loaded_menu = ConfigurableCollide2VTriggerMenu(threshold_config=result.threshold_config_file)
            self.assertEqual(loaded_menu.threshold("SingleTkMuon_22", "muon_pt"), 22.0)
            config = json.loads(result.threshold_config_file.read_text())
            self.assertFalse(config["calibration_complete"])
            self.assertEqual(config["uncalibrated_triggers"], {"SingleTkMuon_22": "unreachable"})
            self.assertIn("target_not_bracketed", config["flagged_triggers"]["SingleTkMuon_22"])
            self.assertFalse(config["export_unreachable_boundary"])
            self.assertEqual(config["exported_boundary_triggers"], [])

    def test_boundary_export_preserves_multileg_ratios_and_fixed_construction_cuts(self) -> None:
        cases = [
            ("DoubleTkMuon_15_7", 2.0, 40_000.0,
             {"leading_muon_pt": 2.0, "subleading_muon_pt": 2.0 * 7 / 15}),
            ("PuppiHT_450", 0.0, 1_000.0, {"ht": 10.0, "ht_jet_pt_min": 30.0}),
        ]
        for name, lower, target, expected in cases:
            with self.subTest(trigger=name), tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
                root = Path(temporary)
                inputs = root / "inputs"
                inputs.mkdir()
                write_events(inputs / "events.parquet")
                result = calibrate_menu(
                    inputs, root / "output", triggers=[name], n_files=1,
                    lower_bound_gev=lower, upper_bound_gev=10,
                    target_rates_khz={name: target}, export_unreachable_boundary=True,
                    make_plots=False, progress=False,
                )
                config = json.loads(result.threshold_config_file.read_text())
                row = json.loads(result.summary_file.read_text())["triggers"][name]
                self.assertEqual(result.unreachable_triggers, (name,))
                self.assertFalse(config["calibration_complete"])
                self.assertEqual(config["calibrated_triggers"], [])
                self.assertEqual(config["exported_boundary_triggers"], [name])
                self.assertTrue(row["boundary_threshold_exported"])
                self.assertIsNone(row["calibrated_threshold_gev"])
                self.assertIn("target_not_bracketed", row["quality"]["flags"])
                menu = ConfigurableCollide2VTriggerMenu(threshold_config=result.threshold_config_file)
                for field, value in expected.items():
                    self.assertAlmostEqual(menu.threshold(name, field), value)
                    self.assertAlmostEqual(row["applied_thresholds_gev"][field], value)
                self.assertEqual(row["boundary_threshold_gev"], lower if name.startswith("Double") else 10)

    def test_cli_boundary_export_resumes_custom_scores_and_can_be_switched_off(self) -> None:
        from collide2v_trigger import NoIsolationCollide2VTriggerMenu

        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            # One passing event / 8,000 gives 5 kHz even at zero pT, below 20 kHz.
            pq.write_table(pa.table({
                "L1T_PhotonTight_PT": [[30.0, 30.0]] + [[] for _ in range(7999)],
                "L1T_PhotonTight_Eta": [[0.0, 0.0]] + [[] for _ in range(7999)],
                "L1T_PhotonTight_Phi": [[0.0, math.pi]] + [[] for _ in range(7999)],
            }), inputs / "events.parquet")
            name = "DoublePhoton_Mass50_200"
            definition = Path(__file__).resolve().parents[1] / "custom_triggers/diphoton_mass_50_200.py"
            output = root / "output"
            options = dict(triggers=[name], custom_trigger_files=[definition], ignore_isolation=True,
                           n_files=1, score_tolerance_gev=0.01, make_plots=False, progress=False)
            initial = calibrate_menu(inputs, output, **options)
            score = next((output / name / "scores").glob("*.parquet"))
            score_mtime = score.stat().st_mtime_ns
            self.assertEqual(json.loads(initial.threshold_config_file.read_text())["thresholds"][name]["photon_pt"], 1)
            command = [
                sys.executable, "-m", "collide2v_trigger.runners.calibrate_menu", str(inputs),
                "-o", str(output), "--trigger", name, "--custom-trigger-file", str(definition),
                "--no-isolation", "--n-files", "1", "--score-tolerance", "0.01",
                "--no-plots", "--quiet", "--export-unreachable-boundary",
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            config = json.loads(initial.threshold_config_file.read_text())
            summary = json.loads(initial.summary_file.read_text())
            row = summary["triggers"][name]
            self.assertEqual(config["thresholds"][name]["photon_pt"], 0)
            self.assertEqual(config["exported_boundary_triggers"], [name])
            self.assertEqual(summary["exported_boundary_triggers"], [name])
            self.assertEqual(row["reused_score_files"], 1)
            self.assertEqual(score.stat().st_mtime_ns, score_mtime)
            self.assertEqual(row["status"], "unreachable")
            self.assertEqual(row["measured_rate_khz"], 5)
            self.assertFalse(config["calibration_complete"])
            self.assertIn("target_not_bracketed", config["flagged_triggers"][name])
            loaded = NoIsolationCollide2VTriggerMenu(custom_trigger_files=[definition],
                                                    threshold_config=initial.threshold_config_file)
            self.assertEqual(loaded.threshold(name, "photon_pt"), 0)
            with patch(f"{MODULE}.scan_pt_thresholds", side_effect=AssertionError("Reuse scores")), \
                 patch(f"{MODULE}.list_parquet_files", side_effect=AssertionError("Reuse manifest")):
                restored = calibrate_menu(inputs, output, **options)
            config = json.loads(restored.threshold_config_file.read_text())
            self.assertEqual(config["thresholds"][name]["photon_pt"], 1)
            self.assertEqual(config["exported_boundary_triggers"], [])

    def test_trigger_failure_is_recorded_and_other_seeds_continue(self) -> None:
        from collide2v_trigger.calibration.threshold_scan import scan_pt_thresholds as real_scan

        def maybe_fail(source, name, output, **kwargs):
            if name == "SingleTkMuon_22":
                raise OSError("Simulated input failure")
            return real_scan(source, name, output, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            write_events(inputs / "minbias.parquet")
            with patch(f"{MODULE}.scan_pt_thresholds", side_effect=maybe_fail), redirect_stdout(io.StringIO()):
                result = calibrate_menu(
                    inputs, root / "output",
                    triggers=["SingleTkMuon_22", "SinglePuppiJet_230"],
                    target_rates_khz={"SinglePuppiJet_230": 10_000.0},
                    n_files=1, make_plots=False, progress=False,
                    export_unreachable_boundary=True,
                )
            self.assertEqual(result.failed_triggers, ("SingleTkMuon_22",))
            self.assertEqual(result.calibrated_triggers, ("SinglePuppiJet_230",))
            summary = json.loads(result.summary_file.read_text())
            self.assertIn("Simulated input failure", summary["triggers"]["SingleTkMuon_22"]["error"])
            self.assertFalse(summary["triggers"]["SingleTkMuon_22"]["boundary_threshold_exported"])
            config = json.loads(result.threshold_config_file.read_text())
            self.assertEqual(config["thresholds"]["SingleTkMuon_22"], {"muon_pt": 22.0})

    def test_interrupted_score_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.parquet"
            target = root / "scores.parquet"
            write_events(source)
            with patch(
                "collide2v_trigger.calibration.threshold_scan._critical_threshold",
                side_effect=[(20.0, "inside"), RuntimeError("Interrupted")],
            ):
                with self.assertRaisesRegex(RuntimeError, "Interrupted"):
                    scan_pt_thresholds(source, "SingleTkMuon_22", target, batch_size=1)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".parquet.tmp").exists())
            scan_pt_thresholds(source, "SingleTkMuon_22", target)
            self.assertEqual(pq.ParquetFile(target).metadata.num_rows, 4)


if __name__ == "__main__":
    unittest.main()
