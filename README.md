# COLLIDE-2V trigger proxies

This repository applies reviewable proxy implementations of CMS Phase-2 L1
trigger seeds to COLLIDE-2V Parquet events. It supports the dataset's dedicated
object collections, a particle-ID-based alternative, configurable transverse
threshold profiles, batched Parquet processing, and direct `root://` EOS input.

For a practical walkthrough, see the [user guide](docs/USER_GUIDE.md) or its
[PDF edition](docs/USER_GUIDE.pdf). It includes an example that runs without EOS.

## Repository layout

```text
src/collide2v_trigger/
  core/       Trigger rules and threshold configuration
  calibration/ Event scores, rate scans, and complete-menu calibration
  io/         Local and EOS Parquet access
  plotting/   Reusable threshold-curve plotting
  runners/    Installed command-line applications
scripts/
  analysis/   One-off trigger discrepancy studies
  plotting/   Plot and diagnostic utilities
  data/       Dataset download helpers
configs/      Threshold profiles and source-file manifests
custom_triggers/ Self-contained external trigger definitions
docs/         Investigation notes
references/   External Phase-2 L1 material
tests/        Small tests independent of the local datasets
data/         Local Parquet files; ignored by Git
results/      Generated reports and plots; ignored by Git
```

## Installation

Create an editable development installation from the repository root:

```bash
python -m pip install -e .
```

For direct EOS input or plotting support:

```bash
python -m pip install -e '.[eos,plots]'
```

XRootD credentials and any required CERN authentication must already work in
the active environment.

## Applying triggers

Use the built-in default thresholds:

```bash
collide2v-apply-triggers data/minbias/input.parquet -o results/triggered.parquet
```

Load a partial threshold profile; omitted triggers retain their defaults:

```bash
collide2v-apply-triggers \
  'root://eosproject-f.cern.ch//eos/path/to/input.parquet' \
  --threshold-config configs/toy_threshold_overrides.json \
  -o results/triggered.parquet
```

Run the particle-ID-based proxy:

```bash
collide2v-apply-id-triggers input.parquet -o results/triggered_id.parquet
```

Trigger outputs are compact sidecars rather than copies of the input event
file. They contain source provenance, `Event_Number` when available, the 33
individual trigger flags, and `Trigger_passes_any`. The input URI, menu name,
and threshold profile are stored as Parquet metadata. To retain another small
column explicitly, repeat `--keep-column`:

```bash
collide2v-apply-triggers input.parquet \
  --keep-column Event_Weight \
  --keep-column Event_ProcessID \
  -o results/triggered.parquet
```

Calculate efficiencies across one or more trigger-bit files:

```bash
collide2v-trigger-efficiencies 'data/minbias/*trigger_bits*.parquet' \
  -o results/efficiencies.txt
```

Scan one trigger on one file and save one critical reference threshold per
event. An ordinary `/eos/...` line from `minbias_files.txt` is converted to a
`root://eosproject.cern.ch` URL automatically:

```bash
collide2v-scan-thresholds \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias/minbias-NEVENT10000-RS59000001.parquet \
  DoubleTkMuon_15_7 \
  --lower-bound 0 --upper-bound 1000 --tolerance 0.25 \
  -o results/minbias_RS59000001_DoubleTkMuon_15_7_threshold.parquet
```

The output has exactly one data column, `pt_threshold_gev`, in the same row
order as the input. For multileg triggers, all rate-setting transverse
thresholds are scaled together relative to their configured values. The
largest configured threshold is the reference coordinate by default; use
`--reference-field` and repeated `--scale-field` options to override this.
The fixed jet floor used to construct HT (`ht_jet_pt_min`) is not scaled.
`NaN` means an event fails at the lower bound because of a fixed condition; a
value equal to the upper bound is right-censored because the event still passes
there. Scan settings and source provenance are stored in Parquet metadata.

The same operation is callable from Python:

```python
from collide2v_trigger.calibration import scan_pt_thresholds

result = scan_pt_thresholds(
    input_file="/eos/project/f/foundational-model-dataset/samples/production_final/minbias/minbias-NEVENT10000-RS59000001.parquet",
    trigger_name="SinglePuppiJet_230",
    output_file="results/single_jet_threshold.parquet",
    tolerance_gev=0.25,
)
print(result)
```

To select and scan several files directly below an EOS directory, use the
directory wrapper. It scans 10 files by default and uses a reproducible random
sample so the result is not tied to the first run-number shards:

```bash
collide2v-scan-eos-directory \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias \
  SinglePuppiJet_230 \
  -n 10 \
  --random-seed 12345 \
  --tolerance 0.25 \
  -o results/single_jet_thresholds
```

The wrapper excludes EOS `.sys.v#.` entries, writes one one-column sidecar per
source file, and skips existing outputs so interrupted jobs can be resumed.
Use `--selection first` for the first N filenames or `--fail-existing` to make
an existing output an error.

Calibrate a threshold from every compatible score file in one local directory.
The trigger name and scan configuration are read from Parquet metadata, and the
target rate defaults to the rate stored in the trigger menu:

```bash
collide2v-calibrate-threshold results/single_jet_thresholds \
  --method bisection \
  --scan-points results/single_jet_thresholds/bisection_points.txt
```

The result is printed and saved by default as
`<trigger>_<method>_calibrated_threshold.json` inside the input directory. To
evaluate a regular threshold grid instead, provide its spacing:

```bash
collide2v-calibrate-threshold results/single_jet_thresholds \
  --method monotonic \
  --spacing 1.0 \
  --target-rate-khz 37 \
  --scan-points results/single_jet_thresholds/monotonic_points.txt
```

The scan-points text file contains threshold, efficiency, rate in kHz, passing
events, total events, and a self-contained metadata header describing the
trigger, reference field, collision rate, selected point, and default menu
threshold. The default `at_most` selection chooses the lowest evaluated
threshold whose measured rate does not exceed the target; use
`--selection-policy closest` to minimize the absolute rate difference instead.

Plot the saved rate curve with its target rate, efficiency scale, selected
threshold, event count, statistical rate granularity, and menu reference:

```bash
collide2v-plot-threshold-scan \
  results/single_jet_thresholds/monotonic_points.txt
```

The default view crops a long all-zero tail; pass `--full-range` to display the
entire scan. Older scan files are supported by automatically reading the
matching calibration JSON from the same directory. The reusable Python
function is:

```python
from collide2v_trigger.plotting import plot_threshold_scan

plot_threshold_scan(
    "results/single_jet_thresholds/monotonic_points.txt",
    "results/single_jet_thresholds/monotonic_points.png",
)
```

## Calibrating a complete menu

Run the complete workflow for all 33 implementable triggers on the same 10
randomly selected minimum-bias files. Rates default to the menu's per-seed
targets, with a 40 MHz collision rate:

```bash
collide2v-calibrate-menu \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias \
  -n 10 \
  --score-tolerance 0.25 \
  --tolerance 0.25 \
  --spacing 1.0 \
  -o results/menu_calibration
```

If a new command has not yet been installed, run it from the repository root:

```bash
PYTHONPATH=src python -m collide2v_trigger.runners.calibrate_menu \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias \
  -o results/menu_calibration
```

Each trigger saves its per-event scores, bisection result, and a monotonic
efficiency/rate curve. Both rate scans read the saved local scores. The plot
marks the **bisection** threshold used in the exported menu, even if the
monotonic grid selects a different threshold. Multileg thresholds are scaled
together using their base ratios, while fixed cuts such as `ht_jet_pt_min`
retain their base values.

```text
results/menu_calibration/
  input_manifest.json
  calibrated_thresholds.json
  calibration_summary.json
  SinglePuppiJet_230/
    scores/<source>_SinglePuppiJet_230_pt_threshold.parquet
    bisection.json
    bisection_points.txt
    monotonic.json
    monotonic_points.txt
    monotonic_points.png
  <other triggers>/...
```

Load the combined JSON directly into the trigger evaluator:

```bash
collide2v-apply-triggers input.parquet \
  --threshold-config results/menu_calibration/calibrated_thresholds.json \
  -o results/calibrated_trigger_bits.parquet
```

Or use the reusable function and menu class:

```python
from collide2v_trigger import ConfigurableCollide2VTriggerMenu
from collide2v_trigger.calibration import calibrate_menu

result = calibrate_menu(
    "root://eosproject.cern.ch//eos/project/f/foundational-model-dataset/samples/production_final/minbias",
    "results/menu_calibration",
    n_files=10,
)
menu = ConfigurableCollide2VTriggerMenu(
    threshold_config=result.threshold_config_file
)
```

Repeat the same command to resume. Completed scores are checked and reused;
interrupted scores are written again. The manifest preserves the original
sample without relisting EOS. Changes to file selection, trigger selection,
base thresholds, or event-scoring settings require a new output directory.
You can change the curve spacing, rate-bisection tolerance, collision rate,
and plotting options while reusing scores. `--threshold-config` supplies an
optional base profile; `--trigger NAME` can be repeated for a subset of seeds.
Use `--no-plots` when plotting dependencies are unavailable.
If you set nondefault isolation options, pass the same options when applying
the calibrated menu; threshold JSON records them as provenance and loads only
the transverse threshold overrides.

The 9 unsupported menu entries are recorded with their missing-collection
reasons. If a target lies outside the rate range covered by the score bounds,
the trigger is marked `unreachable` and keeps its base thresholds in the
combined JSON by default. Add `--export-unreachable-boundary` to export the
bisection boundary for these seeds instead (Python: `export_unreachable_boundary=True`).
Scaled fields keep their base ratios, and fixed cuts remain unchanged. The
trigger still has `status: unreachable`, its calibrated threshold remains null
in the summary, `calibration_complete` remains false, and its quality flags
remain present. `boundary_threshold_exported` and `exported_boundary_triggers`
record which boundary cuts were applied. Repeating the same command with this
option reuses the saved event scores; it does not require rescanning EOS.
A failed trigger always keeps its base thresholds; other seeds
continue unless `--fail-fast` is used. The CLI exits with code 1 if any trigger
fails. The combined JSON records which triggers were calibrated, which were
skipped, and which retained base values. The summary is updated after each seed
and records event counts and rate granularity. These are empirical per-seed
rate calibrations; the total menu OR rate requires a separate evaluation.

Calibration outputs also carry advisory quality flags in each result JSON,
scan text header, console report, and plot. The combined profile contains
`calibration_quality`, `flagged_triggers`, and `quality_review_required`.
Search status and numerical thresholds retain their existing meaning;
`calibrated` means a bracketed search result. Quality flags describe whether
that result needs review:

- `zero_target` and `threshold_undetermined`: zero targets need an explicit
  interpretation; a sample with no passes at the lower bound cannot determine
  a preferred cut. A zero threshold does not disable a seed.
- `zero_acceptance` and `rate_mismatch`: a positive target gives no accepted
  events, or the achieved rate differs from the target beyond the reporting
  tolerance.
- `low_statistics`: fewer than 25 events pass by default.
- `target_not_bracketed`, `near_lower_bound`, `near_upper_bound`,
  `no_events_at_lower_bound`, and `right_censored_scores`: scan-range or
  boundary cases.

The default rate-match tolerance is the larger of 10% of the target rate and
one event's rate contribution. These reporting settings are independent of
the threshold search tolerance and can be changed in either calibration CLI:

```bash
collide2v-calibrate-menu /eos/path/to/minbias \
  --rate-tolerance-relative 0.05 \
  --rate-tolerance-khz 0.4 \
  --min-passing-events 100 \
  -o results/menu_calibration
```

Use `--min-passing-events 0` to disable only the low-count check. Zero targets
require zero observed passing events for numerical rate agreement and are
always flagged for review. Quality flags do not change the selected thresholds
or disable triggers. `ok` means these checks pass on the fitting sample;
it does not establish independence, representative pileup, or held-out rate
agreement. The reported `1/sqrt(passing_events)` statistical estimate assumes
independent events and ignores file correlations.

The toy profile contains illustrative values and is not a calibrated CMS or
COLLIDE-2V menu.

## Diphoton invariant-mass distribution

Plot exactly 100 minimum-bias files directly from EOS:

```bash
python scripts/plotting/plot_diphoton_mass.py \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias \
  --n-files 100 \
  -o results/minbias_diphoton_mass_100files.png
```

The script defaults to that EOS directory and 100 files. Sampling uses the same
deterministic random selection (`--random-seed 12345`) as calibration. To reuse
the exact sample from a 100-file calibration run without relisting EOS:

```bash
python scripts/plotting/plot_diphoton_mass.py \
  --input-manifest results/diphoton_no_isolation/input_manifest.json \
  --n-files 100 \
  -o results/minbias_diphoton_mass_100files.png
```

Only `L1T_PhotonTight_PT`, `Eta`, and `Phi` are read, in batches. No events are
copied locally. The massless-photon formula is
`m² = 2*pT1*pT2*(cosh(eta1-eta2) - cos(phi1-phi2))`.
By default all distinct pairs contribute, without additional pT, eta, or
isolation cuts, so this checks whether any stored pair reaches 120–130 GeV.
Use `--pair-mode leading` for only the two highest-pT photons, `--pt-min 1` for
a common photon threshold, or `--eta-max 2.4` for the central acceptance of the
built-in diphoton seed. These options do not apply calibrated asymmetric pT cuts.

The plot highlights the inclusive 120–130 GeV window. Histogram entries count
pairs; the separately reported window efficiency and 40 MHz rate count each
event once, including empty events in the denominator. A companion `.json`
contains bin counts, overflow counts, per-file counts, selection settings, and
the complete input-file list. The default display is 0–200 GeV with 100 bins
and logarithmic counts; `--mass-max`, `--bins`, and `--linear-y` adjust it.
Window counts include all selected masses, even outside the plotted range.
The program fails if fewer than the requested number of files are available;
use `--n-files 7` explicitly for a seven-file local test. Existing outputs
require `--overwrite`.

The reusable functions are `collect_diphoton_mass_histogram` and
`plot_diphoton_mass` in `collide2v_trigger.plotting.diphoton_mass`.

## Menu without isolation cuts

`NoIsolationCollide2VTriggerMenu` subclasses the configurable menu and bypasses
the shared electron/photon isolation helper. It inherits the same trigger names,
default thresholds, target rates, and all other trigger rules. Even triggers
with `Iso` in their names now use only the helper's pT and eta requirements;
the names are retained for interface compatibility. Isolation columns are no
longer required for built-in seeds, and nonfinite isolation values do not reject
objects. The input collections' upstream ID and selection remain in place.
External trigger definitions retain their own explicit rules.

```python
from collide2v_trigger import NoIsolationCollide2VTriggerMenu

menu = NoIsolationCollide2VTriggerMenu(
    # Same optional threshold_config, threshold_overrides, and custom_trigger_files.
)
passed = menu.evaluate("DoubleTkIsoPhoton_22_12", event)
```

The object evaluator and all scoring/calibration runners select this class with
`--no-isolation`:

```bash
collide2v-apply-triggers input.parquet --no-isolation \
  -o results/trigger_bits_no_isolation.parquet

collide2v-calibrate-menu \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias \
  --no-isolation --n-files 10 \
  -o results/menu_calibration_no_isolation

collide2v-apply-triggers input.parquet --no-isolation \
  --threshold-config results/menu_calibration_no_isolation/calibrated_thresholds.json \
  -o results/calibrated_bits_no_isolation.parquet
```

The same flag works in `collide2v-scan-thresholds` and
`collide2v-scan-eos-directory`. Their Python functions, `calibrate_menu`, and
`create_trigger_menu` accept `ignore_isolation=True`. Without this flag the
existing menu keeps its isolation cuts. This option does not change the
separate ID-based implementation.

Use a new output directory when changing the isolation setting: event scores
must be recomputed. Scores, profiles, scan tables, and output trigger bits record
`isolation_enabled`. Aggregation and resume reject incompatible settings, and
a profile exported by this variant must be applied with the same variant.
Older partial threshold JSON files without an explicit isolation setting remain
loadable through either class.

## External custom triggers

The revised custom diphoton definition uses **[50,200] GeV**, default photon pT
**1 GeV**, and target rate **20 kHz**, matching the original diphoton menu seed.
See [`custom_triggers/README.md`](custom_triggers/README.md) for calibration
commands using `diphoton_mass_50_200.py` / `DoublePhoton_Mass50_200`.
The earlier narrow-window example below is retained for reproducibility.

Put the rule and its metadata in one Python file exporting a `TriggerDefinition`
as `TRIGGER` (or a list/tuple as `TRIGGERS`). The included example is
[`custom_triggers/diphoton_mass_120_130.py`](custom_triggers/diphoton_mass_120_130.py).
It requires any pair of distinct `L1T_PhotonTight` objects to satisfy:

- Both photon pT values are at least the same adjustable `photon_pt` cut,
  default **1 GeV**.
- **120 <= diphoton invariant mass <= 130 GeV**, using massless photon
  four-vectors and the stored PT, Eta, and Phi. Both boundaries are inclusive.
- Target rate **10 kHz**; at **40 MHz** this is efficiency **0.025%**.

No additional eta or isolation requirement is imposed beyond the input photon
collection. All pairs are considered; the photons need not be the leading two.
Only the photon pT cut moves during calibration; the mass window stays fixed.

Each definition contains its name/version/description, required columns,
`evaluate(event, thresholds, fixed_parameters)` callable, numeric default
thresholds, target rate in kHz, fixed parameters, scaled fields, and reference
field. Keep trigger-specific logic and constants in that file. See the example
for a complete definition and `core/external.py` for validation rules.
Evaluation must be deterministic. Critical-score calibration additionally
requires `monotonic=True`: increasing all declared scaled thresholds together
must never turn a failing event into a passing event. This is an author-supplied
contract, not a property that the framework can prove for arbitrary Python.
Rules without that declaration can be evaluated but cannot use this calibration.

The existing menu interface works with external files:

```python
from collide2v_trigger import ConfigurableCollide2VTriggerMenu

menu = ConfigurableCollide2VTriggerMenu(
    custom_trigger_files=["custom_triggers/diphoton_mass_120_130.py"],
    # Optional: threshold_config="results/diphoton/calibrated_thresholds.json",
    # Optional: threshold_overrides={"DoublePhoton_Mass120_130": {"photon_pt": 20.0}},
)
passed = menu.evaluate("DoublePhoton_Mass120_130", event)
print(menu.thresholds_for("DoublePhoton_Mass120_130"))
print(menu.target_rate_khz("DoublePhoton_Mass120_130"))
```

Constructor overrides take precedence over JSON, then the file's defaults.
Omitting external files preserves the existing built-in menu. Duplicate trigger
names are rejected. The ID-based menu is a separate implementation; external
definitions are registered with the configurable object menu.

Evaluate just this seed and save compact trigger bits:

```bash
collide2v-apply-triggers input.parquet \
  --custom-trigger-file custom_triggers/diphoton_mass_120_130.py \
  --trigger DoublePhoton_Mass120_130 \
  -o results/diphoton_bits.parquet
```

Run the whole calibration workflow on 10 files from EOS:

```bash
collide2v-calibrate-menu \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias \
  --custom-trigger-file custom_triggers/diphoton_mass_120_130.py \
  --trigger DoublePhoton_Mass120_130 \
  --n-files 10 --score-tolerance 0.01 --spacing 1 \
  -o results/diphoton
```

Load the resulting JSON together with the same trigger definition:

```bash
collide2v-apply-triggers input.parquet \
  --custom-trigger-file custom_triggers/diphoton_mass_120_130.py \
  --threshold-config results/diphoton/calibrated_thresholds.json \
  --trigger DoublePhoton_Mass120_130 \
  -o results/diphoton_calibrated_bits.parquet
```

`--custom-trigger-file` is repeatable and is also supported by
`collide2v-scan-thresholds` and `collide2v-scan-eos-directory`. Their Python
functions, and `calibrate_menu`, accept `custom_trigger_files=[...]`.
Without `--trigger`, the apply and menu-calibration runners include all built-in
implementable seeds and all loaded custom seeds. For custom-only input files,
only the definition's declared columns are required and read.

Score sidecars include the target rate, complete definition metadata, fixed
cuts, and SHA-256 of the Python file. Rate calibration and threshold plots need
only the saved scores or scan tables, without importing the external file:

```bash
collide2v-calibrate-threshold results/diphoton/DoublePhoton_Mass120_130/scores \
  --method monotonic --spacing 1 \
  --scan-points results/diphoton/custom_points.txt
collide2v-plot-threshold-scan results/diphoton/custom_points.txt
```

Aggregation rejects scores with different definitions. Resuming a menu run or
loading its profile also checks definition identity. Moving an unchanged file
is allowed; editing its content requires a new scoring run. An unreachable
target retains the default/base pT cut and is marked for review, as for built-in
seeds. The configured target is not a promise that this fixed selection can
reach that rate on a given dataset.

Validation of this example: the automated synthetic 8,000-event sample reaches
10 kHz with two accepted events at a cut near 30 GeV. This tests the numerical
interface and has low statistics. The seven local minbias files (70,000 events)
contain no photon pair in the mass window even with a zero pT cut; the full
workflow correctly marks 10 kHz unreachable and retains 1 GeV. An independent
all-pairs calculation confirmed that result. Generated outputs are under
`results/custom_diphoton_smoke/`.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
