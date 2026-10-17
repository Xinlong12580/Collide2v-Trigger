from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from collide2v_trigger import NoIsolationCollide2VTriggerMenu, create_trigger_menu
from collide2v_trigger.calibration import calibrate_menu


ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "custom_triggers/diphoton_mass_50_200.py"
NAME = "DoublePhoton_Mass50_200"


def event(pt, phi=None):
    return {"L1T_PhotonTight_PT": pt, "L1T_PhotonTight_Eta": [0.0] * len(pt),
            "L1T_PhotonTight_Phi": [0.0, math.pi] if phi is None else phi}


class WideDiphotonTests(unittest.TestCase):
    def test_inclusive_mass_boundaries_pt_cut_and_target(self):
        menu = create_trigger_menu(custom_trigger_files=[FILE], ignore_isolation=True)
        self.assertEqual(menu.target_rate_khz(NAME), menu.target_rate_khz("DoubleTkIsoPhoton_22_12"))
        self.assertEqual(menu.thresholds_for(NAME), {"photon_pt": 1.0})
        for pt, expected in [([25, 25], True), ([100, 100], True), ([62.5, 62.5], True),
                             ([24.99, 24.99], False), ([100.01, 100.01], False),
                             ([0.5, 125**2 / 2], False), ([math.nan, 62.5], False)]:
            with self.subTest(pt=pt):
                self.assertEqual(menu.evaluate(NAME, event(pt)), expected)
        self.assertFalse(menu.evaluate(NAME, event([], phi=[])))
        self.assertTrue(menu.evaluate(NAME, event([30, 100, 30], phi=[0, 0.1, math.pi])))
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            menu.evaluate(NAME, event([50, 50], phi=[0]))

    def test_wide_and_narrow_definitions_coexist_and_are_distinct(self):
        old = ROOT / "custom_triggers/diphoton_mass_120_130.py"
        menu = create_trigger_menu(custom_trigger_files=[old, FILE])
        self.assertEqual(len(menu.all_triggers), 44)
        self.assertTrue(menu.evaluate(NAME, event([30, 30])))
        self.assertFalse(menu.evaluate("DoublePhoton_Mass120_130", event([30, 30])))
        self.assertEqual(menu.target_rate_khz("DoublePhoton_Mass120_130"), 10)
        self.assertEqual(menu.custom_trigger_provenance[NAME]["definition"]["version"], "2")

    def test_complete_calibration_roundtrip_with_20khz_target(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            inputs = root / "inputs"
            inputs.mkdir()
            rows = [event([low, 125**2 / (4 * low)]) for low in [10, 20, 30, 40, 50, 55, 60, 62]]
            rows += [event([], phi=[])] * 7992
            pq.write_table(pa.table({key: [row[key] for row in rows] for key in rows[0]}), inputs / "events.parquet")
            result = calibrate_menu(inputs, root / "output", custom_trigger_files=[FILE],
                                    triggers=[NAME], n_files=1, ignore_isolation=True,
                                    upper_bound_gev=100, score_tolerance_gev=0.01,
                                    make_plots=False, progress=False, fail_fast=True)
            self.assertEqual(result.calibrated_triggers, (NAME,))
            summary = json.loads(result.summary_file.read_text())["triggers"][NAME]
            self.assertEqual(summary["target_rate_khz"], 20)
            self.assertEqual(summary["measured_rate_khz"], 20)
            self.assertEqual(summary["passing_events"], 4)
            menu = NoIsolationCollide2VTriggerMenu(custom_trigger_files=[FILE],
                                                 threshold_config=result.threshold_config_file)
            self.assertTrue(40 < menu.threshold(NAME, "photon_pt") < 40.02)
            self.assertEqual(sum(menu.evaluate(NAME, row) for row in rows), 4)
            config = json.loads(result.threshold_config_file.read_text())
            definition = config["custom_trigger_definitions"][NAME]["definition"]
            self.assertEqual(definition["fixed_parameters"], {"mass_min_gev": 50, "mass_max_gev": 200})


if __name__ == "__main__":
    unittest.main()
