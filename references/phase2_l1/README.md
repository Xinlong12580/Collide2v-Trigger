# CMS Phase-2 Level-1 trigger menu and performance references

Last reviewed: 2026-09-20

This guide collects the public CMS documents most relevant to implementing or
validating a Phase-2 Level-1 (L1) trigger menu.  It distinguishes complete menu
definitions from later object-performance and firmware studies.  That
distinction matters: later studies may supersede an object algorithm without
publishing a new, complete, overlap-aware menu.

The live [CMS Level-1 Trigger Public Performance Results index](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1TriggerDPGResults)
is the authoritative entry point for new DPS notes.  The references below are a
curated snapshot of that index as reviewed on the date above.
[Machine-readable citations](references.bib) are provided in BibTeX format.

## Source hierarchy

| Priority | Source | What it establishes | How to use it |
|---|---|---|---|
| 1 | [CMS-TDR-021, *The Phase-2 Upgrade of the CMS Level-1 Trigger*](https://cds.cern.ch/record/2714892) | Baseline Phase-2 L1 architecture, object algorithms, offline-to-online mapping, and the complete 41-seed simplified menu in Table 4.1 | Use when a coherent full menu and overlap-aware total rate are required |
| 2 | [CMS-TDR-022, *The Phase-2 Upgrade of the CMS Data Acquisition and High Level Trigger*](https://cds.cern.ch/record/2759072) | A modified subset of L1 seeds used to study the Phase-2 HLT; Table 10.6 is not presented as a replacement full L1 menu | Use only when reproducing the HLT-TDR study configuration |
| 3 | CMS Detector Performance Summary (DPS) notes and peer-reviewed algorithm papers | Newer reconstruction, firmware, rate, resolution, and efficiency results for individual objects | Use to update a named object algorithm after checking compatibility with the chosen menu snapshot |
| 4 | [Phase-2 L1 integration instructions](https://twiki.cern.ch/twiki/bin/view/CMSPublic/SWGuideL1TPhase2Instructions) | CMSSW integration history and implementation changes | Use to locate software versions; do not treat the change log as a physics menu definition |
| Historical | [CMS-TDR-017, Phase-2 L1 Interim TDR](https://cds.cern.ch/record/2283192) | Pre-2020 design and performance studies | Use for historical context only when TDR-021 contains a later result |

No public source found in this review supplies a newer complete menu with all
seed definitions, correlations, and a replacement overlap-aware total rate.
The later documents below should therefore be treated as algorithm updates or
R&D studies unless they explicitly state otherwise.

## Threshold and rate semantics

CMS-TDR-021 does not generally apply the table's displayed threshold directly
to the L1 object.  It derives an offline-to-online scaling curve from trigger
turn-ons.  The displayed offline threshold is defined at:

- 95% of the efficiency plateau for muons, electrons/photons, and jets;
- 90% for taus, HT, and missing transverse momentum;
- an additional 50% point is quoted for the slower tau, HT, and MET turn-ons.

The TDR menu rates assume 14 TeV collisions and an average pileup of 200.
Rates are inclusive per seed, so they cannot be summed.  Table 4.1 reports:

- 41 seeds;
- 346 kHz for their overlap-aware OR;
- 450 kHz after the table's 30% allowance.

The study discusses a 500 kHz design target within the 750 kHz maximum L1
output bandwidth.  The 450 kHz number is the table's stated menu total after
its allowance; it is not the arithmetic sum of the individual seed rates.

## Two commonly mixed menu snapshots

CMS-TDR-022 says that its Table 10.6 is a subset of the TDR-021 simplified menu
with minor modifications.  Several values therefore differ.  These examples
are enough to identify which source a configuration follows:

| Seed or object | CMS-TDR-021 Table 4.1 | CMS-TDR-022 Table 10.6 | Interpretation |
|---|---:|---:|---|
| Single PUPPI jet | 180 GeV, 70 kHz | 230 GeV, 35 kHz | Both are offline-threshold labels; the 230 GeV version is still a narrow R=0.4 PUPPI-jet seed |
| Double PUPPI jet | 112/112 GeV, 71 kHz | 112/112 GeV, 75 kHz | Same nominal thresholds, updated study rate |
| PUPPI HT | 450 GeV, 11 kHz | 450 GeV, 11 kHz | Slow turn-on; TDR-021 also quotes a 377 GeV 50%-efficiency point |
| PUPPI MET | 200 GeV, 18 kHz | 220 GeV, 18 kHz | TDR-022 states that the 220 GeV offline point corresponds to an online requirement near 119 GeV |
| Tau seed | Double PUPPI tau 52/52 GeV, 7 kHz | Double HPS@L1 tau 21/21 GeV, 9 kHz | Different tau algorithms and working points |
| Single track muon | 22 GeV, 12 kHz | 22 GeV, 12 kHz | Stable between these tables |
| Single track electron | 36 GeV, 24 kHz | 36 GeV, 24 kHz | Stable between these tables |
| Single isolated track photon | 36 GeV, 43 kHz | 36 GeV, 43 kHz | Stable between these tables |

CMS-TDR-021 Table 4.1 should be consulted directly for the full seed list,
including all topology, eta, relative-z, charge, and mass requirements.  The
complete baseline categories are single/multiple leptons, photons, calorimeter
and PUPPI taus, PUPPI jets/HT/MET, lepton cross triggers, VBF, and B-physics.

## Core design and menu documents

### CMS-TDR-021 — Level-1 Trigger TDR (2020)

- Record: CERN-LHCC-2020-004; CMS-TDR-021.
- [CDS record and versioned files](https://cds.cern.ch/record/2714892)
- [Accessible PDF mirror](https://lss.fnal.gov/archive/2020/pub/fermilab-pub-20-712-cms.pdf)
- Read Chapter 3 for object algorithms and Chapter 4 for menu construction.
- Table 4.1 on document pages 210–211 is the principal complete menu table.
- Section 4.1 defines offline-to-online scaling.
- Section 4.4 studies rate stability at pileup 140, 200, 250, and 300.

This is the primary citation for a coherent Phase-2 L1 menu.  It is a design
baseline, not a promise that every object algorithm remained unchanged after
2020.

### CMS-TDR-022 — DAQ and HLT TDR (2021)

- Record: CERN-LHCC-2021-007; CMS-TDR-022.
- [CDS record](https://cds.cern.ch/record/2759072)
- [CMS public-results record](https://cms-results.web.cern.ch/cms-results/public-results/preliminary-results/TDR-21-001/index.html)
- Chapter 10 describes the Phase-2 HLT reconstruction study.
- Table 10.6 lists the L1 seeds used to preselect that HLT study.
- The surrounding text explicitly calls this a subset with minor modifications
  and explains the offline-threshold convention.

Use this source for the `PuppiJet_230`/`PuppiMET_220` configuration and HLT
performance projections.  Do not combine its modified seed values with the
TDR-021 total menu rate.

### CMS-TDR-017 — Interim L1 TDR (2017)

- Record: CERN-LHCC-2017-013; CMS-TDR-017.
- [CDS record and PDF](https://cds.cern.ch/record/2283192)

This document contains earlier Phase-2 projections.  It is valuable for design
history and detector motivation, but TDR-021 should take precedence for the
2020 baseline menu.

## Particle flow, PUPPI, tracking, and primary vertex

### Particle-flow reconstruction for the CMS Phase-II L1 trigger (2023)

- C. Herwig for CMS, JINST 18 (2023) C01037.
- DOI: [10.1088/1748-0221/18/01/C01037](https://doi.org/10.1088/1748-0221/18/01/C01037)
- [INSPIRE record](https://inspirehep.net/literature/2624932)

This is the compact post-TDR reference for the Correlator PF/PUPPI processing,
FPGA implementation, and the downstream construction of leptons, photons,
jets, and energy sums.

### Primary-vertex and track-to-vertex reconstruction

- [End-to-end neural-network PV and track association](https://cds.cern.ch/record/2792619), CMS-DP-2021-035.
- [Updated Phase-2 L1 vertexing performance](https://cds.cern.ch/record/2814727), CMS-DP-2022-020.
- [*Neural Network-Based Primary Vertex Reconstruction with FPGAs for the Upgrade of the CMS Level-1 Trigger System*](https://cds.cern.ch/record/2801638), CMS-CR-2022-018; J. Phys. Conf. Ser. 2438 (2023) 012106.
- [CMS public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/Level1TriggerPhase2E2ENNForPV)
- [*Track Finding and Neural Network-Based Primary Vertex Reconstruction with FPGAs*](https://cds.cern.ch/record/2816274), CMS-CR-2022-100.

These sources are essential when validating pileup rejection.  They describe
the intended task of identifying the hard-scatter vertex from L1 tracks, rather
than fixing PUPPI to a generator-labelled interaction.

## Jets, HT, and MET

### Seeded-cone PUPPI jets — CMS-DP-2023-023

- [CDS record](https://cds.cern.ch/record/2859652)
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1TSCDPSNote2023)
- Related proceedings: S. Summers, I. Bestintzanos, and G. Petrucciani,
  *Reconstructing jets in the Phase-2 upgrade of the CMS Level-1 Trigger with a
  Seeded Cone algorithm*, EPJ Web Conf. 295 (2024) 02024,
  [DOI](https://doi.org/10.1051/epjconf/202429502024).

This is the principal post-TDR jet-algorithm reference.  It defines SC4 and
SC8, compares them with anti-kT jets made from the same L1 PUPPI particles,
and uses SC4 for baseline performance.  The public results state that R=0.8
jets were not then used in the baseline Phase-2 menu.

### Improved L1 MET — CMS-DP-2025-023

- [CDS record](https://cds.cern.ch/record/2932976)
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1Phase2JUMP)

This note studies the JUMP MET algorithm, Type-I-like jet corrections, MET
significance, firmware feasibility, and rates measured with PU=200 minimum-bias
simulation.  It is an algorithm-development result, not a replacement full
menu.

### Wide-cone jets and jet mass — CMS-DP-2025-080

- [CDS PDF](https://cds.cern.ch/record/2948916/files/DP2025_080.pdf)
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1TJetMassDPSNote2025)

This note studies SC8 jet mass and possible menu integration.  Its baseline
comparison explicitly identifies the existing single-jet seed as an SC4 PUPPI
jet with a 230 GeV offline threshold.  It should not be used to reinterpret
`PuppiJet_230` as an AK8 seed.

### Additional jet tagging studies

- [Neural-network L1 b tagging](https://cds.cern.ch/record/2814728), CMS-DP-2022-021.
- [Multiclass object tagging](https://cds.cern.ch/record/2936315), CMS-DP-2025-032.
- [Multiclass object tagging and jet-pT regression](https://cds.cern.ch/record/2961842), CMS-DP-2026-037.
- [Wide-cone jet substructure tagging](https://cds.cern.ch/record/2967022), CMS-DP-2026-110; [public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1TWideConeSubstructureTaggingDPSNote2026).

These extend the physics reach of jet triggers.  Their thresholds and rates
belong to the particular study configurations and should not be inserted into
the TDR-021 menu without recomputing the full OR and overlaps.

## Electrons, photons, and calorimeter objects

### Electron reconstruction and identification — CMS-DP-2023-047

- [CDS PDF and record](https://cds.cern.ch/record/2868782/files/DP2023_047.pdf)

This is the first major post-TDR update to the track-matched electron
identification and pileup-resistant isolation algorithms.

### Updated electron identification — CMS-DP-2024-098

- [CDS PDF](https://cds.cern.ch/record/2916193/files/DP2024_098.pdf)
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1TP2EGDPSNote2024)

This note updates the 2023 study with machine-learning approaches aimed at
improving low-pT electron efficiency at controlled rate.

### Standalone e/gamma, calorimeter jets, and calorimeter taus — CMS-DP-2024-057

- [CDS PDF](https://cds.cern.ch/record/2904693/files/DP2024_057.pdf)
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/Level1Phase2CaloTriggerNote2024)

This is the relevant firmware-emulation reference for standalone calorimeter
objects, including the StaEG-like path absent from the supplied COLLIDE-2V
branches.

### Low-latency electron pT regression — CMS-DP-2025-059

- [CDS PDF](https://cds.cern.ch/record/2942558/files/DP2025_059.pdf)
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1TP2EleRegressionDPSNote2025)

This is a later R&D result for barrel track-matched electron momentum
regression.  It is useful for object response, but it does not define a full
menu revision.

## Tau triggers

### Calorimeter TauMinator — CMS-DP-2023-062

- [CDS record](https://cds.cern.ch/record/2868783)
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/Phase2NNCaloTauMinator)

The note describes a calorimeter-only neural-network tau algorithm.  CMS notes
that tracker information is needed to reach the lower offline thresholds
targeted by the menu, while the calorimeter-only algorithm is useful at higher
thresholds.

### Neural-network PUPPI tau

- [CDS record](https://cds.cern.ch/record/2895660), CMS-DP-2024-018.
- [Public performance page](https://twiki.cern.ch/twiki/bin/view/CMSPublic/L1Phase2NNPuppiTau)

This study updates the TDR PUPPI-tau reconstruction, input seeding, neural
network identification, and firmware implementation.  Its rate-tuning example
uses a study-specific target and must be kept separate from the older TDR-021
DoublePuppiTau entry and the HLT-TDR HPS@L1 seed.

## Muon triggers

Muon object definitions and the complete menu rates remain documented most
coherently in CMS-TDR-021.  Useful later subsystem studies include:

- [*The role of the RPCs in the CMS Level-1 Trigger during Phase-2*](https://cds.cern.ch/record/2910236), CMS-DP-2024-079, for EMTF++ and the effect of RPC/iRPC inputs.
- [*Deep Learning for the Level-1 ME0 Trigger in the CMS Experiment*](https://cds.cern.ch/record/2932518), CMS-DP-2025-017, for very-forward muons and minimum-bias-rate rejection.

These are subsystem or extended-acceptance results.  They do not provide a
replacement for the TDR TkMuon seed definitions and their menu correlations.

## Global-trigger optimization and nonstandard signatures

- [*Optimizing cut-based algorithms to specific physics acceptance regions at the CMS Level-1 Trigger*](https://cds.cern.ch/record/2916192), CMS-DP-2024-097, studies joint rate/efficiency tuning in the Phase-2 Global Trigger.
- [*Displaced Vertex Track Trigger for the CMS Phase-2 Level-1 Trigger Upgrade*](https://cds.cern.ch/record/2901311), CMS-DP-2024-032, is a simulation feasibility study for displaced-track vertices.
- [Continual learning in the CMS Phase-2 L1 trigger](https://cds.cern.ch/record/2859651), CMS-DP-2023-022, studies adaptation to detector degradation rather than a menu definition.

These are useful algorithm-design references.  They do not publish a new
overlap-aware conventional menu.

## L1 scouting and 40 MHz studies

- [*Level-1 trigger scouting in Phase-2*](https://cds.cern.ch/record/2916191), CMS-DP-2024-096.
- [*Extending the Features of the CMS Phase-2 Level-1 Trigger Scouting*](https://cds.cern.ch/record/2942557), CMS-DP-2025-058.
- [*Soft Tau Reconstruction and R&D on Accelerators in CMS Phase-2 Level-1 Trigger Scouting*](https://cds.cern.ch/record/2962039), CMS-DP-2026-043.
- [*Search For Low-Mass Dielectron Resonances with Phase-2 L1 Scouting*](https://cds.cern.ch/record/2968115), CMS-DP-2026-115.
- [A 40 MHz L1 scouting system for the CMS Phase-2 upgrade](https://doi.org/10.1016/j.nima.2022.167805), Nucl. Instrum. Meth. A 1047 (2023) 167805.
- [Development and demonstration of the CMS Phase-2 L1 scouting baseline system](https://doi.org/10.1088/1748-0221/21/01/C01024), JINST 21 (2026) C01024.

These are relevant to event generation, full-rate object streams, and novel
low-threshold analyses.  They are not conventional accept/reject menu
definitions.

## Official Phase-2 DPS index snapshot

The CMS L1 public-results page currently indexes the following formal Phase-2
performance notes.  This compact list makes the scope of the collection
explicit; the most menu-relevant items are annotated above.

| Year | DPS notes in the official Phase-2 L1 index |
|---|---|
| 2021 | [DP-2021-035, PV and track-to-vertex association](https://cds.cern.ch/record/2792619) |
| 2022 | [DP-2022-020, primary vertex](https://cds.cern.ch/record/2814727); [DP-2022-021, b tagging](https://cds.cern.ch/record/2814728) |
| 2023 | [DP-2023-022, continual learning](https://cds.cern.ch/record/2859651); [DP-2023-023, seeded-cone jets](https://cds.cern.ch/record/2859652); [DP-2023-047, electrons](https://cds.cern.ch/record/2868782); [DP-2023-062, calorimeter taus](https://cds.cern.ch/record/2868783) |
| 2024 | [DP-2024-018, NNPuppiTau](https://cds.cern.ch/record/2895660); [DP-2024-032, displaced vertices](https://cds.cern.ch/record/2901311); [DP-2024-057, standalone calorimeter objects](https://cds.cern.ch/record/2904693); [DP-2024-079, RPCs in the L1 muon trigger](https://cds.cern.ch/record/2910236); [DP-2024-096, L1 scouting](https://cds.cern.ch/record/2916191); [DP-2024-097, cut optimization](https://cds.cern.ch/record/2916192); [DP-2024-098, electrons](https://cds.cern.ch/record/2916193) |
| 2025 | [DP-2025-023, MET](https://cds.cern.ch/record/2932976); [DP-2025-032, multiclass tagging](https://cds.cern.ch/record/2936315); [DP-2025-058, scouting](https://cds.cern.ch/record/2942557); [DP-2025-059, electron pT regression](https://cds.cern.ch/record/2942558); [DP-2025-080, jet mass](https://cds.cern.ch/record/2948916) |
| 2026 | [DP-2026-037, tagging and jet-pT regression](https://cds.cern.ch/record/2961842); [DP-2026-043, soft taus in scouting](https://cds.cern.ch/record/2962039); [DP-2026-110, wide-cone tagging](https://cds.cern.ch/record/2967022); [DP-2026-115, low-mass dielectrons in scouting](https://cds.cern.ch/record/2968115) |

The official index also links a Phase-2 topological CNN study without a CDS DPS
record.  Detector-subsystem notes on tracker, calorimeter, and muon trigger
primitives are outside this menu-focused list unless they directly define or
measure a final L1 object.  The ME0 study annotated above is one such
supplementary muon-subsystem reference and is not part of this index snapshot.

## What to cite for common claims

| Claim | Preferred source |
|---|---|
| Complete baseline seed list and overlap-aware menu rate | CMS-TDR-021, Table 4.1 |
| Meaning of offline thresholds and turn-on conversion | CMS-TDR-021 Section 4.1; CMS-TDR-022 text preceding Table 10.6 |
| `PuppiJet_230` and the L1 subset used by HLT studies | CMS-TDR-022, Table 10.6 |
| Narrow-cone Phase-2 PUPPI jet algorithm and SC4/AK4 comparison | CMS-DP-2023-023 |
| Wide-cone SC8/AK8 development | CMS-DP-2025-080 and the 2026 wide-cone study |
| PF/PUPPI Correlator implementation | JINST 18 (2023) C01037 |
| Reconstructed L1 primary-vertex algorithms | CMS-CR-2022-018 and CMS-CR-2022-100 |
| Current electron-object development | CMS-DP-2024-098; CMS-DP-2025-059 |
| Standalone calorimeter e/gamma, jet, and tau objects | CMS-DP-2024-057 |
| Tau algorithm development | CMS-DP-2023-062 and the NNPuppiTau public study |
| New MET developments | CMS-DP-2025-023 |
| Rate/efficiency cut optimization | CMS-DP-2024-097 |
| Complete and current list of CMS L1 performance notes | CMS Level-1 Trigger Public Performance Results index |

## Recommended use in this repository

Maintain separate, named configurations rather than one mixed `TriggerInfo`
table:

1. `cms_tdr_021_table_4_1`: exact 41-seed 2020 baseline.
2. `cms_tdr_022_table_10_6`: exact modified subset used by the 2021 HLT study.
3. Optional object-study profiles such as `cms_dp_2023_023_sc4` or
   `cms_dp_2025_023_jump`, each with its own sample and rate assumptions.
4. `collide2v_proxy`: dataset-specific branch mapping and calibrated proxy
   cuts, with no CMS menu total attached unless the same seed set and object
   definitions have been validated.

For every seed, store the source ID, table or section, offline threshold,
online cut, turn-on convention, object working point, pileup assumption,
individual rate, and menu profile.  A rate from one profile should never be
combined with thresholds or objects from another profile without a new
minimum-bias evaluation of the full seed OR.
