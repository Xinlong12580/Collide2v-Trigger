# Custom diphoton definitions

| File | Trigger name | Fixed mass window | Default photon pT | Target rate |
|---|---|---|---|---|
| `diphoton_mass_50_200.py` | `DoublePhoton_Mass50_200` | [50,200] GeV, inclusive | 1 GeV for both | 20 kHz |
| `diphoton_mass_120_130.py` | `DoublePhoton_Mass120_130` | [120,130] GeV, inclusive | 1 GeV for both | 10 kHz |

The wider-window definition is the revised custom trigger (version 2). The
narrow-window file is retained so its previous score files and profiles can
still be reproduced. Each file contains its full rule and metadata. All pairs
of `L1T_PhotonTight` objects are considered, with no added eta or isolation cut.
The mass window stays fixed when calibrating the common photon pT threshold.

Calibrate the revised trigger to the original diphoton seed's **20 kHz target**
at 40 MHz, using the same file-selection settings as its 100-file run:

```bash
conda activate hep

collide2v-calibrate-menu \
  /eos/project/f/foundational-model-dataset/samples/production_final/minbias \
  --custom-trigger-file custom_triggers/diphoton_mass_50_200.py \
  --trigger DoublePhoton_Mass50_200 \
  --no-isolation \
  --n-files 100 --selection random --random-seed 12345 \
  --collision-rate-mhz 40 \
  --score-tolerance 0.01 --tolerance 0.01 --spacing 0.25 \
  -o results/diphoton_mass50_200_20khz
```

Use this fresh output directory: the wider window needs new event scores.
The trigger definition supplies the 20 kHz target automatically. The original
trigger's previous fit achieved 19.96 kHz against that same 20 kHz target.
Matching file samples additionally requires the source directory listing to
remain unchanged.

Apply the resulting profile:

```bash
collide2v-apply-triggers /path/to/ggHgammagamma.parquet \
  --custom-trigger-file custom_triggers/diphoton_mass_50_200.py \
  --trigger DoublePhoton_Mass50_200 --no-isolation \
  --threshold-config results/diphoton_mass50_200_20khz/calibrated_thresholds.json \
  -o results/ggh_diphoton_mass50_200_bits.parquet
```

The existing `results/minbias_diphoton_mass_100files.json` histogram contains
291 pairs in this wider window among 1,000,000 events, before extra pT cuts.
Therefore the maximum event rate on that sample is at most **11.64 kHz**:
an event can contribute multiple pairs. A 20 kHz target is unreachable there by
pT tuning alone. The calibrator reports this and retains the default/base pT
cut by default. Add `--export-unreachable-boundary` to apply its boundary cut
instead; the target remains marked unreachable. This option can be added when
resuming the same output directory, reusing its existing scores.
Inspect `calibration_summary.json`
before interpreting the exported profile as calibrated.
