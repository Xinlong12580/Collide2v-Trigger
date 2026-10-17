from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from collide2v_trigger.plotting import plot_threshold_scan, read_threshold_scan
except ModuleNotFoundError:
    PLOTTING_AVAILABLE = False
else:
    PLOTTING_AVAILABLE = True


@unittest.skipUnless(PLOTTING_AVAILABLE, "matplotlib plotting extra is unavailable")
class ThresholdPlotTests(unittest.TestCase):
    def test_self_describing_scan_is_parsed_and_plotted(self) -> None:
        content = """\
# format: rate-scan-points-v2
# trigger: SinglePuppiJet_230
# method: monotonic
# reference_field: jet_pt
# scale_fields: ["jet_pt"]
# base_thresholds_gev: {"jet_pt":230.0}
# target_rate_khz: 4000
# collision_rate_mhz: 40
# calibrated_threshold_gev: 20
# measured_efficiency: 0.1
# measured_rate_khz: 4000
# passing_events: 1
# total_events: 10
# rate_resolution_khz: 4000
# selection_policy: at_most
# threshold_profile: default
threshold_gev efficiency rate_khz passing_events total_events
0 0.2 8000 2 10
20 0.1 4000 1 10
40 0 0 0 10
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "points.txt"
            output = Path(temporary_directory) / "points.png"
            source.write_text(content, encoding="utf-8")

            scan = read_threshold_scan(source)
            saved = plot_threshold_scan(source, output)

            self.assertEqual(scan.trigger_name, "SinglePuppiJet_230")
            self.assertEqual(scan.reference_field, "jet_pt")
            self.assertEqual(scan.selected_threshold_gev, 20.0)
            self.assertEqual(scan.base_thresholds_gev["jet_pt"], 230.0)
            self.assertEqual(saved, output)
            self.assertGreater(output.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
