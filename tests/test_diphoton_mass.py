import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from collide2v_trigger.plotting.diphoton_mass import collect_diphoton_mass_histogram, plot_diphoton_mass
from collide2v_trigger.runners.plot_diphoton_mass import select_inputs


ROOT = Path(__file__).resolve().parents[1]


def write_events(path, pts, phis=None, etas=None):
    phis = phis if phis is not None else [[0.0, math.pi] if len(row) == 2 else [] for row in pts]
    etas = etas if etas is not None else [[0.0] * len(row) for row in pts]
    # Deliberately store the columns in an order different from the read request.
    pq.write_table(pa.table({"L1T_PhotonTight_Phi": phis,
                             "L1T_PhotonTight_Eta": etas,
                             "L1T_PhotonTight_PT": pts}), path)


class DiphotonMassTests(unittest.TestCase):
    def test_known_masses_window_event_count_and_histogram_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "events.parquet"
            write_events(source, [[60, 60], [65, 65], [62.5, 62.5, 62.5], [70, 70], []],
                         [[0, math.pi], [0, math.pi], [0, math.pi, math.pi], [0, math.pi], []])
            result = collect_diphoton_mass_histogram([source], mass_min_gev=10, mass_max_gev=130,
                                                     bins=12, batch_size=2)
            self.assertEqual(result["events"], 5)
            self.assertEqual(result["pairs"], 6)
            self.assertEqual(result["window_pairs"], 4)
            self.assertEqual(result["window_events"], 3)
            self.assertEqual(result["window_event_efficiency"], 3 / 5)
            self.assertEqual(result["window_event_rate_khz"], 24000)
            self.assertEqual(result["underflow_pairs"], 1)
            self.assertEqual(result["overflow_pairs"], 1)
            self.assertEqual(sum(result["histogram"]["pair_counts"]), 4)
            self.assertEqual(result["maximum_mass_gev"], 140)
            output = plot_diphoton_mass(result, Path(tmp) / "mass.png", dpi=60)
            self.assertGreater(output.stat().st_size, 1000)

    def test_all_pairs_and_leading_pair_can_give_different_window_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "events.parquet"
            write_events(source, [[62.5, 100, 62.5]], [[0, 0.5, math.pi]])
            all_pairs = collect_diphoton_mass_histogram([source])
            leading = collect_diphoton_mass_histogram([source], pair_mode="leading")
            self.assertEqual(all_pairs["pairs"], 3)
            self.assertEqual(all_pairs["window_events"], 1)
            self.assertEqual(leading["pairs"], 1)
            self.assertEqual(leading["window_events"], 0)
            thresholded = collect_diphoton_mass_histogram([source], pt_min_gev=63)
            self.assertEqual(thresholded["pairs"], 0)

    def test_invalid_photons_eta_cut_empty_and_misaligned_collections(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "events.parquet"
            write_events(source, [[62.5, 62.5, math.nan, -1], []],
                         [[0, math.pi, 0, 0], []], [[0, 2.4, 0, 0], []])
            result = collect_diphoton_mass_histogram([source], eta_max=2.4)
            self.assertEqual(result["events"], 2)
            self.assertEqual(result["invalid_photons"], 2)
            self.assertEqual(result["pairs"], 0)
            self.assertIsNone(result["maximum_mass_gev"])
            plot_diphoton_mass(result, Path(tmp) / "empty.png", dpi=60)
            write_events(source, [[62.5, 62.5]], [[0]])
            with self.assertRaisesRegex(ValueError, "aligned lists"):
                collect_diphoton_mass_histogram([source])

    def test_duplicate_sources_and_missing_columns_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "events.parquet"
            write_events(source, [[60, 60]])
            alias = Path(tmp) / "alias.parquet"
            alias.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "Duplicate input files"):
                collect_diphoton_mass_histogram([source, alias])
            pq.write_table(pa.table({"Trigger_passes_any": [True]}), source)
            with self.assertRaisesRegex(ValueError, "missing photon columns"):
                collect_diphoton_mass_histogram([source])

    def test_manifest_preserves_order_normalizes_eos_and_requires_enough_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"selected_files": [
                "/eos/example/minbias-02.parquet", "root://host//eos/example/minbias-01.parquet"]}))
            args = argparse.Namespace(input_manifest=manifest, input_directory=None,
                                      n_files=2, eos_endpoint="root://host")
            files, metadata = select_inputs(args)
            self.assertEqual(files, ["root://host//eos/example/minbias-02.parquet",
                                     "root://host//eos/example/minbias-01.parquet"])
            self.assertEqual(metadata["selection"], "manifest_order")
            args.n_files = 100
            with self.assertRaisesRegex(ValueError, "Requested 100 files"):
                select_inputs(args)

    def test_cli_processes_exactly_100_files_and_saves_self_describing_histogram(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "inputs"
            inputs.mkdir()
            for index in range(101):
                write_events(inputs / f"minbias-{index:03d}.parquet", [[62.5, 62.5], []])
            output = root / "mass.png"
            command = [sys.executable, str(ROOT / "scripts/plotting/plot_diphoton_mass.py"),
                       str(inputs), "--selection", "first", "--dpi", "60", "-o", str(output)]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(result["file_count"], 100)
            self.assertEqual(len(result["per_file"]), 100)
            self.assertEqual(result["events"], 200)
            self.assertEqual(result["window_events"], 100)
            self.assertEqual(sum(result["histogram"]["pair_counts"]), 100)
            self.assertEqual(result["input_selection"]["requested_n_files"], 100)
            self.assertGreater(output.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
