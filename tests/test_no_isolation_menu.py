from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from collide2v_trigger import (
    ConfigurableCollide2VTriggerMenu, NoIsolationCollide2VTriggerMenu, create_trigger_menu,
)
from collide2v_trigger.calibration import (
    calibrate_menu, discover_threshold_score_files, scan_eos_directory, scan_pt_thresholds,
)


NAME = "DoubleTkIsoPhoton_22_12"
ROOT = Path(__file__).resolve().parents[1]


def event():
    row = {name: [] for name in ConfigurableCollide2VTriggerMenu().required_columns}
    for prefix, pt, eta, phi, extra in [
        ("L1T_Electron", [40.0], [0.2], [0.0], {"DZ": [0.0]}),
        ("L1T_MuonTight", [25.0], [0.1], [2.0], {"DZ": [0.0], "Charge": [1]}),
        ("L1T_PhotonTight", [50.0, 30.0], [0.1, 0.2], [0.0, math.pi], {}),
        ("L1T_JetPuppiAK4", [400.0, 400.0], [0.0, 0.0], [0.0, math.pi], {"Mass": [0.0, 0.0]}),
    ]:
        for key, values in {"PT": pt, "Eta": eta, "Phi": phi, **extra}.items():
            row[f"{prefix}_{key}"] = values
    row["L1T_Electron_IsolationVarRhoCorr"] = [math.nan]
    row["L1T_PhotonTight_IsolationVarRhoCorr"] = [math.nan, math.inf]
    row["L1T_PUPPIMET_MET"] = [300.0]
    return row


def write_events(path, *, include_isolation):
    row = event()
    fields = (ConfigurableCollide2VTriggerMenu() if include_isolation
              else NoIsolationCollide2VTriggerMenu()).required_columns
    pq.write_table(pa.table({key: [row[key]] for key in fields}), path)


class NoIsolationMenuTests(unittest.TestCase):
    def test_all_isolated_seeds_bypass_isolation_and_other_seeds_match(self):
        standard = ConfigurableCollide2VTriggerMenu()
        relaxed = NoIsolationCollide2VTriggerMenu()
        row = event()
        isolated = {"SingleTkIsoElectron_28", "SingleTkIsoPhoton_36", NAME,
                    "TkMuon_TkIsoElectron_7_20", "TkIsoElectron_PuppiHT_26_190"}
        for name in relaxed.implementable_triggers:
            with self.subTest(trigger=name):
                if name in isolated:
                    self.assertFalse(standard.evaluate(name, row))
                    self.assertTrue(relaxed.evaluate(name, row))
                else:
                    self.assertEqual(standard.evaluate(name, row), relaxed.evaluate(name, row))
        self.assertEqual(standard.effective_thresholds, relaxed.effective_thresholds)
        self.assertEqual(standard.all_triggers, relaxed.all_triggers)
        self.assertEqual(standard.target_rate_khz(NAME), relaxed.target_rate_khz(NAME))
        self.assertFalse(any("Isolation" in name for name in relaxed.required_columns))
        self.assertFalse(any("Isolation" in name for name in relaxed.required_columns_for(NAME)))

    def test_pt_eta_overrides_and_custom_rule_remain_effective(self):
        row = event()
        row = {key: value for key, value in row.items() if "Isolation" not in key}
        menu = NoIsolationCollide2VTriggerMenu()
        self.assertTrue(menu.evaluate(NAME, row))
        row["L1T_PhotonTight_Eta"] = [0.1, 2.4]
        self.assertFalse(menu.evaluate(NAME, row))
        row["L1T_PhotonTight_Eta"] = [0.1, 0.2]
        menu = NoIsolationCollide2VTriggerMenu(threshold_overrides={NAME: {"leading_photon_pt": 51}})
        self.assertFalse(menu.evaluate(NAME, row))
        custom = ROOT / "custom_triggers/diphoton_mass_120_130.py"
        for ignore in [True, False]:
            menu = create_trigger_menu(ignore_isolation=ignore, custom_trigger_files=[custom])
            self.assertFalse(menu.evaluate("DoublePhoton_Mass120_130", row))
            row["L1T_PhotonTight_PT"] = [62.5, 62.5]
            row["L1T_PhotonTight_Eta"] = [0.0, 0.0]
            self.assertTrue(menu.evaluate("DoublePhoton_Mass120_130", row))
            row = event()

    def test_scoring_needs_no_isolation_columns_and_rejects_mixed_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "events.parquet"
            write_events(source, include_isolation=True)
            scores = root / "scores"
            scores.mkdir()
            scan_pt_thresholds(source, NAME, scores / "standard.parquet")
            scan_pt_thresholds(source, NAME, scores / "relaxed.parquet", ignore_isolation=True)
            standard = pq.read_table(scores / "standard.parquet")
            relaxed = pq.read_table(scores / "relaxed.parquet")
            self.assertTrue(math.isnan(standard.column(0)[0].as_py()))
            self.assertTrue(math.isfinite(relaxed.column(0)[0].as_py()))
            self.assertEqual(relaxed.schema.metadata[b"collide2v_trigger.isolation_enabled"], b"false")
            with self.assertRaisesRegex(ValueError, "inconsistent scan configurations"):
                discover_threshold_score_files(scores)
            write_events(source, include_isolation=False)
            scan_pt_thresholds(source, NAME, root / "without_iso_columns.parquet", ignore_isolation=True)

    def test_menu_profile_resume_and_variant_checks(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            inputs = root / "inputs"
            inputs.mkdir()
            write_events(inputs / "events.parquet", include_isolation=False)
            options = dict(triggers=[NAME], n_files=1, ignore_isolation=True,
                           target_rates_khz={NAME: 40000}, make_plots=False, progress=False)
            result = calibrate_menu(inputs, root / "calibration", **options)
            self.assertEqual(result.calibrated_triggers, (NAME,))
            config = json.loads(result.threshold_config_file.read_text())
            self.assertFalse(config["object_selection"]["isolation_enabled"])
            loaded = NoIsolationCollide2VTriggerMenu(threshold_config=result.threshold_config_file)
            self.assertTrue(loaded.evaluate(NAME, event()))
            with self.assertRaisesRegex(ValueError, "different isolation setting"):
                ConfigurableCollide2VTriggerMenu(threshold_config=result.threshold_config_file)
            resumed = calibrate_menu(inputs, root / "calibration", **options)
            summary = json.loads(resumed.summary_file.read_text())
            self.assertEqual(summary["triggers"][NAME]["reused_score_files"], 1)
            with self.assertRaisesRegex(ValueError, "different.*settings"):
                calibrate_menu(inputs, root / "calibration", **{**options, "ignore_isolation": False})
            scan_eos_directory(inputs, NAME, root / "scores", n_files=1, ignore_isolation=True)
            with self.assertRaisesRegex(ValueError, "different isolation setting"):
                scan_eos_directory(inputs, NAME, root / "scores", n_files=1)

    def test_cli_selects_variant_and_preserves_compact_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "events.parquet", root / "bits.parquet"
            write_events(source, include_isolation=False)
            completed = subprocess.run([
                sys.executable, "-m", "collide2v_trigger.runners.apply_object", str(source),
                "--trigger", NAME, "--no-isolation", "-o", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            table = pq.read_table(output)
            self.assertEqual(table.schema.names, ["source_row", f"Trigger_{NAME}", "Trigger_passes_any"])
            self.assertEqual(table[f"Trigger_{NAME}"].to_pylist(), [True])
            self.assertEqual(table.schema.metadata[b"collide2v_trigger.isolation_enabled"], b"false")


if __name__ == "__main__":
    unittest.main()
