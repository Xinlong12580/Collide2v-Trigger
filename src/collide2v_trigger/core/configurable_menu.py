"""Configurable, human-reviewable COLLIDE-2V trigger-menu proxy.

This class is a drop-in replacement for the dedicated-object menu used by the
object-collection runner. With no threshold configuration it reproduces
that menu's current behavior:

* the same 42 trigger names and 33 implementable trigger proxies;
* the same target-rate metadata and required-column interface;
* relative DZ cuts are converted from the menu's centimetres to the stored
  Delphes millimetres;
* single-object absolute-PV DZ cuts are disabled; and
* PUPPI HT is recomputed from PUPPI AK4 jets.

Only transverse thresholds are configurable.  Eta, isolation, DZ, charge,
mass, Delta-R, and Delta-eta rules remain explicit in the methods below.  A
JSON file may override any subset of the defaults; constructor overrides are
then applied on top of the JSON values.  The precedence is therefore:

    constructor override > JSON override > DEFAULT_THRESHOLDS

The trigger names retain their reference-menu numbers even when an override is
active.  Use ``thresholds_for`` or ``menu_summary`` to inspect the effective
values used by a particular instance.
"""

from __future__ import annotations

from itertools import combinations
import json
import math
from numbers import Real
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .object_menu import (
    Collide2VTriggerMenuObjectCollectionsFullBranches,
    TriggerInfo,
)
from .external import TriggerRegistry, definition_identity


ThresholdMap = Mapping[str, Mapping[str, float]]


class ConfigurableCollide2VTriggerMenu(
    Collide2VTriggerMenuObjectCollectionsFullBranches
):
    """Dedicated-object trigger proxy with configurable transverse thresholds."""

    DZ_CM_TO_MM = 10.0
    isolation_enabled = True

    # This is the single authoritative table of default transverse thresholds.
    # Named fields make the meaning and ordering of every leg explicit.
    DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
        # Single, double, and mixed leptons
        "SingleTkMuon_22": {"muon_pt": 22.0},
        "DoubleTkMuon_15_7": {
            "leading_muon_pt": 15.0,
            "subleading_muon_pt": 7.0,
        },
        "SingleTkElectron_36": {"electron_pt": 36.0},
        "SingleTkIsoElectron_28": {"electron_pt": 28.0},
        "TkIsoElectron_StaEG_22_12": {
            "electron_pt": 22.0,
            "staeg_pt": 12.0,
        },
        "DoubleTkElectron_25_12": {
            "leading_electron_pt": 25.0,
            "subleading_electron_pt": 12.0,
        },
        "SingleStaEG_51": {"staeg_pt": 51.0},
        "DoubleStaEG_37_24": {
            "leading_staeg_pt": 37.0,
            "subleading_staeg_pt": 24.0,
        },
        "SingleTkIsoPhoton_36": {"photon_pt": 36.0},
        "DoubleTkIsoPhoton_22_12": {
            "leading_photon_pt": 22.0,
            "subleading_photon_pt": 12.0,
        },

        # Tau placeholders (kept in the table even though the objects are absent)
        "SingleCaloTau_150": {"calotau_pt": 150.0},
        "DoubleCaloTau_90_90": {
            "leading_calotau_pt": 90.0,
            "subleading_calotau_pt": 90.0,
        },
        "DoublePuppiTau_52_52": {
            "leading_tau_pt": 52.0,
            "subleading_tau_pt": 52.0,
        },

        # Hadronic and MET seeds
        "SinglePuppiJet_230": {"jet_pt": 230.0},
        "DoublePuppiJet_112_112": {
            "leading_jet_pt": 112.0,
            "subleading_jet_pt": 112.0,
        },
        "PuppiHT_450": {"ht": 450.0, "ht_jet_pt_min": 30.0},
        "PuppiHT_135": {"ht": 135.0, "ht_jet_pt_min": 30.0},
        "QuadPuppiJets_PuppiHT_400_70_55_40_40": {
            "ht": 400.0,
            "leading_jet_pt": 70.0,
            "second_jet_pt": 55.0,
            "third_jet_pt": 40.0,
            "fourth_jet_pt": 40.0,
            "ht_jet_pt_min": 25.0,
        },
        "PuppiETmiss_200": {"met": 200.0},

        # Cross-lepton seeds
        "TkMuon_TkIsoElectron_7_20": {
            "muon_pt": 7.0,
            "electron_pt": 20.0,
        },
        "TkMuon_TkElectron_7_23": {
            "muon_pt": 7.0,
            "electron_pt": 23.0,
        },
        "TkElectron_TkMuon_10_20": {
            "electron_pt": 10.0,
            "muon_pt": 20.0,
        },
        "TripleTkMuon_5_3_3": {
            "leading_muon_pt": 5.0,
            "second_muon_pt": 3.0,
            "third_muon_pt": 3.0,
        },
        "TkMuon_DoubleTkElectron_17_17_6": {
            "muon_pt": 17.0,
            "leading_electron_pt": 17.0,
            "subleading_electron_pt": 6.0,
        },
        "DoubleTkMuon_TkElectron_9_5_5": {
            "leading_muon_pt": 9.0,
            "subleading_muon_pt": 5.0,
            "electron_pt": 5.0,
        },
        "PuppiTau_TkMuon_18_42": {"tau_pt": 18.0, "muon_pt": 42.0},
        "TkIsoElectron_PuppiTau_22_45": {
            "electron_pt": 22.0,
            "tau_pt": 45.0,
        },

        # Cross hadronic-leptonic seeds
        "TkMuon_PuppiHT_6_320": {
            "muon_pt": 6.0,
            "ht": 320.0,
            "ht_jet_pt_min": 30.0,
        },
        "TkMuon_TriplePuppiJet_12_40_40_40": {
            "muon_pt": 12.0,
            "leading_jet_pt": 40.0,
            "second_jet_pt": 40.0,
            "third_jet_pt": 40.0,
        },
        "DoubleTkElectron_PuppiHT_8_8_390": {
            "leading_electron_pt": 8.0,
            "subleading_electron_pt": 8.0,
            "ht": 390.0,
            "ht_jet_pt_min": 30.0,
        },
        "DoubleTkMuon_PuppiHT_3_3_300": {
            "leading_muon_pt": 3.0,
            "subleading_muon_pt": 3.0,
            "ht": 300.0,
            "ht_jet_pt_min": 30.0,
        },
        "DoubleTkMuon_PuppiJet_PuppiETmiss_3_3_60_130": {
            "leading_muon_pt": 3.0,
            "subleading_muon_pt": 3.0,
            "jet_pt": 60.0,
            "met": 130.0,
        },
        "TkIsoElectron_PuppiHT_26_190": {
            "electron_pt": 26.0,
            "ht": 190.0,
            "ht_jet_pt_min": 30.0,
        },
        "TkElectron_PuppiJet_28_40": {
            "electron_pt": 28.0,
            "jet_pt": 40.0,
        },
        "PuppiTau_PuppiETmiss_55_190": {"tau_pt": 55.0, "met": 190.0},
        "TkMuon_PuppiJet_PuppiETmiss_3_110_120": {
            "muon_pt": 3.0,
            "jet_pt": 110.0,
            "met": 120.0,
        },

        # VBF and B-physics seeds
        "VBF_DoublePuppiJets_160_35_mjj620": {
            "leading_jet_pt": 160.0,
            "subleading_jet_pt": 35.0,
        },
        "BPH_DoubleTkMuon_2_2": {
            "leading_muon_pt": 2.0,
            "subleading_muon_pt": 2.0,
        },
        "BPH_DoubleTkMuon_4_4_DR1p5": {
            "leading_muon_pt": 4.0,
            "subleading_muon_pt": 4.0,
        },
        "BPH_DoubleTkMuon_4_4_m7_18": {
            "leading_muon_pt": 4.0,
            "subleading_muon_pt": 4.0,
        },
        "BPH_TripleTkMuon_5_3_0_m0_9": {
            "leading_muon_pt": 5.0,
            "second_muon_pt": 3.0,
            "third_muon_pt": 0.0,
        },
        "BPH_TripleTkMuon_5_3_2_m5_17": {
            "leading_muon_pt": 5.0,
            "second_muon_pt": 3.0,
            "third_muon_pt": 2.0,
        },
    }

    # Groups whose names imply an ordering.  Validation catches accidentally
    # reversed legs while still allowing equal thresholds.
    ORDERED_THRESHOLD_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
        "DoubleTkMuon_15_7": (("leading_muon_pt", "subleading_muon_pt"),),
        "DoubleTkElectron_25_12": (
            ("leading_electron_pt", "subleading_electron_pt"),
        ),
        "DoubleTkIsoPhoton_22_12": (
            ("leading_photon_pt", "subleading_photon_pt"),
        ),
        "TripleTkMuon_5_3_3": (
            ("leading_muon_pt", "second_muon_pt", "third_muon_pt"),
        ),
        "TkMuon_DoubleTkElectron_17_17_6": (
            ("leading_electron_pt", "subleading_electron_pt"),
        ),
        "DoubleTkMuon_TkElectron_9_5_5": (
            ("leading_muon_pt", "subleading_muon_pt"),
        ),
        "QuadPuppiJets_PuppiHT_400_70_55_40_40": (
            (
                "leading_jet_pt",
                "second_jet_pt",
                "third_jet_pt",
                "fourth_jet_pt",
            ),
        ),
        "VBF_DoublePuppiJets_160_35_mjj620": (
            ("leading_jet_pt", "subleading_jet_pt"),
        ),
        "BPH_TripleTkMuon_5_3_0_m0_9": (
            ("leading_muon_pt", "second_muon_pt", "third_muon_pt"),
        ),
        "BPH_TripleTkMuon_5_3_2_m5_17": (
            ("leading_muon_pt", "second_muon_pt", "third_muon_pt"),
        ),
    }

    def __init__(
        self,
        *,
        electron_iso_max: float = 0.15,
        photon_iso_max: float = 0.15,
        use_rho_corrected_isolation: bool = True,
        threshold_config: str | Path | None = None,
        threshold_overrides: ThresholdMap | None = None,
        custom_trigger_files: Sequence[str | Path] = (),
    ) -> None:
        """Build a menu using defaults, optional JSON, and optional overrides.

        The first three keyword arguments are identical to the old dedicated-
        object menu.  ``threshold_config`` and ``threshold_overrides`` are new.
        Both may contain a partial set of triggers and threshold fields.
        """
        super().__init__(
            electron_iso_max=electron_iso_max,
            photon_iso_max=photon_iso_max,
            use_rho_corrected_isolation=use_rho_corrected_isolation,
        )
        registry = TriggerRegistry(custom_trigger_files)
        self.custom_trigger_definitions = registry.definitions
        self.custom_trigger_provenance = registry.provenance
        # Instance copies keep other menu instances and the built-in menu intact.
        self.DEFAULT_THRESHOLDS = self._copy_thresholds(type(self).DEFAULT_THRESHOLDS)
        self.ALL_TRIGGERS = list(self.all_triggers)
        self.TRIGGER_INFO = dict(self.trigger_info)
        names = {name.lower() for name in self.ALL_TRIGGERS}
        for name, definition in registry.definitions.items():
            if name.lower() in names:
                raise ValueError(f"Duplicate trigger name: {name}")
            names.add(name.lower())
            self.ALL_TRIGGERS.append(name)
            self.all_triggers.append(name)
            self.implementable_triggers.append(name)
            self.DEFAULT_THRESHOLDS[name] = dict(definition.default_thresholds)
            info = TriggerInfo(
                category="Custom", display_name=name,
                thresholds_gev=tuple(definition.default_thresholds.values()),
                rate_khz=definition.target_rate_khz,
                requirements=definition.description, plateau_percent=(),
                note=f"External definition version {definition.version}",
            )
            self.TRIGGER_INFO[name] = self.trigger_info[name] = info
            self._dispatch[name] = self._external_evaluator(name, definition)
        self.default_thresholds = self._copy_thresholds(self.DEFAULT_THRESHOLDS)
        self._effective_thresholds = self._copy_thresholds(self.DEFAULT_THRESHOLDS)
        self.threshold_config_path: Path | None = None
        self.threshold_profile = "default"
        self.threshold_config_metadata: dict[str, Any] = {}

        if threshold_config is not None:
            path = Path(threshold_config).expanduser()
            file_overrides, metadata = self._read_threshold_config(path)
            isolation = metadata.get("object_selection", {}).get("isolation_enabled")
            if isolation is not None and (not isinstance(isolation, bool) or isolation != self.isolation_enabled):
                raise ValueError("Threshold profile has a different isolation setting; use the matching menu class or --no-isolation option")
            for name, provenance in metadata.get("custom_trigger_definitions", {}).items():
                current = self.custom_trigger_provenance.get(name)
                if current is None:
                    raise ValueError(f"Profile requires external trigger {name}; supply its custom_trigger_files")
                if definition_identity(current) != definition_identity(provenance):
                    raise ValueError(f"External trigger definition changed for {name}; recalibrate this profile")
            self._apply_threshold_overrides(file_overrides, source=str(path))
            self.threshold_config_path = path
            self.threshold_config_metadata = metadata
            self.threshold_profile = str(metadata.get("profile", path.stem))

        if threshold_overrides is not None:
            self._apply_threshold_overrides(
                threshold_overrides, source="constructor threshold_overrides"
            )
            if threshold_config is None:
                self.threshold_profile = "custom"
            else:
                self.threshold_profile += "+custom"

        self._validate_resolved_thresholds()

    def _external_evaluator(self, name, definition):
        return lambda event: definition.evaluate(event, self._cuts(name), definition.fixed_parameters)

    @property
    def _builtin_required_columns(self) -> list[str]:
        return super().required_columns

    @property
    def required_columns(self) -> list[str]:
        definitions = getattr(self, "custom_trigger_definitions", {})
        return sorted(set(self._builtin_required_columns).union(
            *(definition.required_columns for definition in definitions.values())
        ))

    def required_columns_for(self, name: str) -> list[str]:
        if name not in self.all_triggers:
            raise KeyError(f"Unknown trigger: {name}")
        definition = self.custom_trigger_definitions.get(name)
        return list(definition.required_columns) if definition else self._builtin_required_columns

    # ------------------------------------------------------------------
    # Threshold configuration and inspection
    # ------------------------------------------------------------------
    @staticmethod
    def _copy_thresholds(values: ThresholdMap) -> dict[str, dict[str, float]]:
        return {
            trigger: {field: float(value) for field, value in fields.items()}
            for trigger, fields in values.items()
        }

    @classmethod
    def _read_threshold_config(
        cls, path: Path
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        if not path.is_file():
            raise FileNotFoundError(f"Threshold configuration does not exist: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in threshold configuration {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise TypeError(f"Threshold configuration root must be an object: {path}")
        version = raw.get("schema_version", 1)
        if version != 1:
            raise ValueError(
                f"Unsupported threshold schema_version {version!r} in {path}; expected 1"
            )
        overrides = raw.get("thresholds")
        if not isinstance(overrides, dict):
            raise TypeError(f"'thresholds' must be an object in {path}")
        metadata = {key: value for key, value in raw.items() if key != "thresholds"}
        return overrides, metadata

    def _apply_threshold_overrides(
        self, overrides: Mapping[str, Any], *, source: str
    ) -> None:
        if not isinstance(overrides, Mapping):
            raise TypeError(f"Threshold overrides from {source} must be a mapping")
        for trigger, fields in overrides.items():
            if trigger not in self.DEFAULT_THRESHOLDS:
                raise KeyError(f"Unknown trigger in {source}: {trigger}")
            if not isinstance(fields, Mapping):
                raise TypeError(
                    f"Threshold entry for {trigger} in {source} must be a mapping"
                )
            allowed = self.DEFAULT_THRESHOLDS[trigger]
            for field, value in fields.items():
                if field not in allowed:
                    valid = ", ".join(allowed)
                    raise KeyError(
                        f"Unknown threshold field {trigger}.{field} in {source}; "
                        f"valid fields: {valid}"
                    )
                if isinstance(value, bool) or not isinstance(value, Real):
                    raise TypeError(
                        f"Threshold {trigger}.{field} in {source} must be numeric"
                    )
                numeric = float(value)
                if not math.isfinite(numeric) or numeric < 0.0:
                    raise ValueError(
                        f"Threshold {trigger}.{field} in {source} must be finite "
                        "and nonnegative"
                    )
                self._effective_thresholds[trigger][field] = numeric

    def _validate_resolved_thresholds(self) -> None:
        if set(self.DEFAULT_THRESHOLDS) != set(self.ALL_TRIGGERS):
            missing = sorted(set(self.ALL_TRIGGERS) - set(self.DEFAULT_THRESHOLDS))
            extra = sorted(set(self.DEFAULT_THRESHOLDS) - set(self.ALL_TRIGGERS))
            raise RuntimeError(
                f"DEFAULT_THRESHOLDS does not match trigger menu; missing={missing}, "
                f"extra={extra}"
            )
        for trigger, groups in self.ORDERED_THRESHOLD_GROUPS.items():
            values = self._effective_thresholds[trigger]
            for group in groups:
                ordered = [values[field] for field in group]
                if any(left < right for left, right in zip(ordered, ordered[1:])):
                    raise ValueError(
                        f"Threshold legs for {trigger} must be nonincreasing: "
                        + ", ".join(f"{field}={values[field]:g}" for field in group)
                    )

    def _cuts(self, trigger: str) -> Mapping[str, float]:
        """Internal zero-copy access to the resolved thresholds for one seed."""
        return self._effective_thresholds[trigger]

    def threshold(self, trigger: str, field: str) -> float:
        """Return one effective threshold, with clear errors for bad names."""
        if trigger not in self._effective_thresholds:
            raise KeyError(f"Unknown trigger: {trigger}")
        if field not in self._effective_thresholds[trigger]:
            valid = ", ".join(self._effective_thresholds[trigger])
            raise KeyError(f"Unknown threshold {trigger}.{field}; valid fields: {valid}")
        return self._effective_thresholds[trigger][field]

    def thresholds_for(self, trigger: str) -> dict[str, float]:
        """Return a copy of all effective thresholds for one trigger."""
        if trigger not in self._effective_thresholds:
            raise KeyError(f"Unknown trigger: {trigger}")
        return dict(self._effective_thresholds[trigger])

    @property
    def effective_thresholds(self) -> dict[str, dict[str, float]]:
        """Return a defensive copy of the complete resolved threshold table."""
        return self._copy_thresholds(self._effective_thresholds)

    def target_rate_khz(self, trigger: str) -> float:
        """Return unchanged target-rate metadata for a trigger seed."""
        if trigger not in self.trigger_info:
            raise KeyError(f"Unknown trigger: {trigger}")
        return float(self.trigger_info[trigger].rate_khz)

    def menu_summary(self) -> list[dict[str, Any]]:
        """Extend the old summary with default and effective named thresholds."""
        rows = super().menu_summary()
        for row in rows:
            name = row["name"]
            row["default_thresholds"] = dict(self.default_thresholds[name])
            row["effective_thresholds"] = self.thresholds_for(name)
            row["threshold_profile"] = self.threshold_profile
            row["isolation_enabled"] = self.isolation_enabled
        return rows

    # ------------------------------------------------------------------
    # Currently used DZ convention
    # ------------------------------------------------------------------
    @classmethod
    def _dz_for_indices(cls, event, prefix, indices, max_dz=1.0) -> bool:
        # The dataset has no unambiguous reconstructed PV for an absolute
        # single-object DZ requirement.  Relative multi-object DZ remains.
        if len(indices) == 1:
            return True
        return super()._dz_for_indices(
            event, prefix, indices, max_dz=max_dz * cls.DZ_CM_TO_MM
        )

    @classmethod
    def _mixed_dz_compatible(cls, event, objects, max_dz=1.0) -> bool:
        return super()._mixed_dz_compatible(
            event, objects, max_dz=max_dz * cls.DZ_CM_TO_MM
        )

    # ------------------------------------------------------------------
    # Single and double lepton rules
    # ------------------------------------------------------------------
    def trigger_singletkmuon_22(self, event):
        cut = self._cuts("SingleTkMuon_22")
        # At least one tight-muon proxy with pT >= muon_pt and |eta| < 2.4.
        return bool(
            self._object_indices(
                event, self.MUON, pt_min=cut["muon_pt"], eta_max=2.4
            )
        )

    def trigger_doubletkmuon_15_7(self, event):
        cut = self._cuts("DoubleTkMuon_15_7")
        pt = self._field(event, self.MUON, "PT")
        thresholds = (cut["leading_muon_pt"], cut["subleading_muon_pt"])
        mus = self._object_indices(
            event, self.MUON, pt_min=min(thresholds), eta_max=2.4
        )
        return any(
            self._passes_thresholds((pt[i], pt[j]), thresholds)
            and self._dz_for_indices(event, self.MUON, (i, j))
            for i, j in combinations(mus, 2)
        )

    def trigger_singletkelectron_36(self, event):
        cut = self._cuts("SingleTkElectron_36")
        return bool(
            self._object_indices(
                event, self.ELECTRON, pt_min=cut["electron_pt"], eta_max=2.4
            )
        )

    def trigger_singletkisoelectron_28(self, event):
        cut = self._cuts("SingleTkIsoElectron_28")
        return bool(
            self._isolated_indices(
                event,
                self.ELECTRON,
                pt_min=cut["electron_pt"],
                eta_max=2.4,
                iso_max=self.electron_iso_max,
            )
        )

    def trigger_tkisoelectron_staeg_22_12(self, event):
        return self._raise_not_impl("TkIsoElectron_StaEG_22_12")

    def trigger_doubletkelectron_25_12(self, event):
        cut = self._cuts("DoubleTkElectron_25_12")
        pt = self._field(event, self.ELECTRON, "PT")
        thresholds = (
            cut["leading_electron_pt"],
            cut["subleading_electron_pt"],
        )
        els = self._object_indices(
            event, self.ELECTRON, pt_min=min(thresholds), eta_max=2.4
        )
        return any(
            self._passes_thresholds((pt[i], pt[j]), thresholds)
            and self._dz_for_indices(event, self.ELECTRON, (i, j))
            for i, j in combinations(els, 2)
        )

    def trigger_singlestaeg_51(self, event):
        return self._raise_not_impl("SingleStaEG_51")

    def trigger_doublestaeg_37_24(self, event):
        return self._raise_not_impl("DoubleStaEG_37_24")

    # ------------------------------------------------------------------
    # Photon and tau rules
    # ------------------------------------------------------------------
    def trigger_singletkisophoton_36(self, event):
        cut = self._cuts("SingleTkIsoPhoton_36")
        return bool(
            self._isolated_indices(
                event,
                self.PHOTON,
                pt_min=cut["photon_pt"],
                eta_max=2.4,
                iso_max=self.photon_iso_max,
            )
        )

    def trigger_doubletkisophoton_22_12(self, event):
        cut = self._cuts("DoubleTkIsoPhoton_22_12")
        pt = self._field(event, self.PHOTON, "PT")
        thresholds = (cut["leading_photon_pt"], cut["subleading_photon_pt"])
        phs = self._isolated_indices(
            event,
            self.PHOTON,
            pt_min=min(thresholds),
            eta_max=2.4,
            iso_max=self.photon_iso_max,
        )
        return any(
            self._passes_thresholds((pt[i], pt[j]), thresholds)
            for i, j in combinations(phs, 2)
        )

    def trigger_singlecalotau_150(self, event):
        return self._raise_not_impl("SingleCaloTau_150")

    def trigger_doublecalotau_90_90(self, event):
        return self._raise_not_impl("DoubleCaloTau_90_90")

    def trigger_doublepuppitau_52_52(self, event):
        return self._raise_not_impl("DoublePuppiTau_52_52")

    # ------------------------------------------------------------------
    # Hadronic and MET rules
    # ------------------------------------------------------------------
    def trigger_singlepuppijet_230(self, event):
        cut = self._cuts("SinglePuppiJet_230")
        return bool(self._jet_indices(event, pt_min=cut["jet_pt"], eta_max=2.4))

    def trigger_doublepuppijet_112_112(self, event):
        cut = self._cuts("DoublePuppiJet_112_112")
        pt = self._field(event, self.PUPPI_JET, "PT")
        eta = self._field(event, self.PUPPI_JET, "Eta")
        thresholds = (cut["leading_jet_pt"], cut["subleading_jet_pt"])
        jets = self._jet_indices(event, pt_min=min(thresholds), eta_max=2.4)
        return any(
            self._passes_thresholds(
                (pt[i], pt[j]),
                thresholds,
            )
            and abs(float(eta[i]) - float(eta[j])) < 1.6
            for i, j in combinations(jets, 2)
        )

    def trigger_puppiht_450(self, event):
        cut = self._cuts("PuppiHT_450")
        ht = self._puppi_ht(
            event, jet_pt_min=cut["ht_jet_pt_min"], eta_max=2.4
        )
        return ht >= cut["ht"]

    def trigger_puppiht_135(self, event):
        cut = self._cuts("PuppiHT_135")
        ht = self._puppi_ht(
            event, jet_pt_min=cut["ht_jet_pt_min"], eta_max=2.4
        )
        return ht >= cut["ht"]

    def trigger_quadpuppijets_puppiht_400_70_55_40_40(self, event):
        cut = self._cuts("QuadPuppiJets_PuppiHT_400_70_55_40_40")
        pt = self._field(event, self.PUPPI_JET, "PT")
        eta = self._field(event, self.PUPPI_JET, "Eta")
        n = min(len(pt), len(eta))
        eligible = [
            i
            for i in range(n)
            if float(pt[i]) > cut["ht_jet_pt_min"] and abs(float(eta[i])) < 2.4
        ]
        if sum(float(pt[i]) for i in eligible) < cut["ht"]:
            return False
        jet_thresholds = (
            cut["leading_jet_pt"],
            cut["second_jet_pt"],
            cut["third_jet_pt"],
            cut["fourth_jet_pt"],
        )
        return any(
            self._passes_thresholds([pt[i] for i in combo], jet_thresholds)
            for combo in combinations(eligible, 4)
        )

    def trigger_puppietmiss_200(self, event):
        cut = self._cuts("PuppiETmiss_200")
        return self._scalar(event, "L1T_PUPPIMET_MET") >= cut["met"]

    # ------------------------------------------------------------------
    # Cross-lepton rules
    # ------------------------------------------------------------------
    def _configured_mu_e_pair(
        self,
        event,
        *,
        muon_pt: float,
        electron_pt: float,
        isolated_electron: bool,
    ) -> bool:
        mus = self._object_indices(
            event, self.MUON, pt_min=muon_pt, eta_max=2.4
        )
        if isolated_electron:
            els = self._isolated_indices(
                event,
                self.ELECTRON,
                pt_min=electron_pt,
                eta_max=2.4,
                iso_max=self.electron_iso_max,
            )
        else:
            els = self._object_indices(
                event, self.ELECTRON, pt_min=electron_pt, eta_max=2.4
            )
        return any(
            self._mixed_dz_compatible(
                event, ((self.MUON, im), (self.ELECTRON, ie))
            )
            for im in mus
            for ie in els
        )

    def trigger_tkmuon_tkisoelectron_7_20(self, event):
        cut = self._cuts("TkMuon_TkIsoElectron_7_20")
        return self._configured_mu_e_pair(
            event,
            muon_pt=cut["muon_pt"],
            electron_pt=cut["electron_pt"],
            isolated_electron=True,
        )

    def trigger_tkmuon_tkelectron_7_23(self, event):
        cut = self._cuts("TkMuon_TkElectron_7_23")
        return self._configured_mu_e_pair(
            event,
            muon_pt=cut["muon_pt"],
            electron_pt=cut["electron_pt"],
            isolated_electron=False,
        )

    def trigger_tkelectron_tkmuon_10_20(self, event):
        cut = self._cuts("TkElectron_TkMuon_10_20")
        return self._configured_mu_e_pair(
            event,
            muon_pt=cut["muon_pt"],
            electron_pt=cut["electron_pt"],
            isolated_electron=False,
        )

    def trigger_tripletkmuon_5_3_3(self, event):
        # LooseID is deliberately ignored; all other rules are explicit here.
        cut = self._cuts("TripleTkMuon_5_3_3")
        thresholds = (
            cut["leading_muon_pt"],
            cut["second_muon_pt"],
            cut["third_muon_pt"],
        )
        pt = self._field(event, self.MUON, "PT")
        mus = self._object_indices(
            event, self.MUON, pt_min=min(thresholds), eta_max=2.4
        )
        return any(
            self._passes_thresholds([pt[i] for i in combo], thresholds)
            and self._dz_for_indices(event, self.MUON, combo)
            for combo in combinations(mus, 3)
        )

    def trigger_tkmuon_doubletkelectron_17_17_6(self, event):
        cut = self._cuts("TkMuon_DoubleTkElectron_17_17_6")
        electron_thresholds = (
            cut["leading_electron_pt"],
            cut["subleading_electron_pt"],
        )
        ept = self._field(event, self.ELECTRON, "PT")
        mus = self._object_indices(
            event, self.MUON, pt_min=cut["muon_pt"], eta_max=2.4
        )
        els = self._object_indices(
            event,
            self.ELECTRON,
            pt_min=min(electron_thresholds),
            eta_max=2.4,
        )
        for im in mus:
            for i, j in combinations(els, 2):
                if not self._passes_thresholds(
                    (ept[i], ept[j]), electron_thresholds
                ):
                    continue
                objects = (
                    (self.MUON, im),
                    (self.ELECTRON, i),
                    (self.ELECTRON, j),
                )
                if self._mixed_dz_compatible(event, objects):
                    return True
        return False

    def trigger_doubletkmuon_tkelectron_9_5_5(self, event):
        # LooseID is deliberately ignored.
        cut = self._cuts("DoubleTkMuon_TkElectron_9_5_5")
        muon_thresholds = (
            cut["leading_muon_pt"],
            cut["subleading_muon_pt"],
        )
        mpt = self._field(event, self.MUON, "PT")
        mus = self._object_indices(
            event, self.MUON, pt_min=min(muon_thresholds), eta_max=2.4
        )
        els = self._object_indices(
            event, self.ELECTRON, pt_min=cut["electron_pt"], eta_max=2.4
        )
        for i, j in combinations(mus, 2):
            if not self._passes_thresholds((mpt[i], mpt[j]), muon_thresholds):
                continue
            for ie in els:
                objects = (
                    (self.MUON, i),
                    (self.MUON, j),
                    (self.ELECTRON, ie),
                )
                if self._mixed_dz_compatible(event, objects):
                    return True
        return False

    def trigger_puppitau_tkmuon_18_42(self, event):
        return self._raise_not_impl("PuppiTau_TkMuon_18_42")

    def trigger_tkisoelectron_puppitau_22_45(self, event):
        return self._raise_not_impl("TkIsoElectron_PuppiTau_22_45")

    # ------------------------------------------------------------------
    # Cross hadronic-leptonic rules
    # ------------------------------------------------------------------
    def trigger_tkmuon_puppiht_6_320(self, event):
        cut = self._cuts("TkMuon_PuppiHT_6_320")
        ht = self._puppi_ht(
            event, jet_pt_min=cut["ht_jet_pt_min"], eta_max=2.4
        )
        if ht < cut["ht"]:
            return False
        return bool(
            self._object_indices(
                event, self.MUON, pt_min=cut["muon_pt"], eta_max=2.4
            )
        )

    def trigger_tkmuon_triplepuppijet_12_40_40_40(self, event):
        cut = self._cuts("TkMuon_TriplePuppiJet_12_40_40_40")
        mu_eta = self._field(event, self.MUON, "Eta")
        mu_phi = self._field(event, self.MUON, "Phi")
        jet_pt = self._field(event, self.PUPPI_JET, "PT")
        jet_eta = self._field(event, self.PUPPI_JET, "Eta")
        jet_phi = self._field(event, self.PUPPI_JET, "Phi")
        jet_thresholds = (
            cut["leading_jet_pt"],
            cut["second_jet_pt"],
            cut["third_jet_pt"],
        )
        mus = self._object_indices(
            event, self.MUON, pt_min=cut["muon_pt"], eta_max=2.4
        )
        jets = self._jet_indices(
            event, pt_min=min(jet_thresholds), eta_max=2.4
        )
        for im in mus:
            for combo in combinations(jets, 3):
                if not self._passes_thresholds(
                    [jet_pt[index] for index in combo], jet_thresholds
                ):
                    continue
                j1, j2, j3 = sorted(
                    combo, key=lambda index: float(jet_pt[index]), reverse=True
                )
                if self._delta_r(
                    float(jet_eta[j1]),
                    float(jet_phi[j1]),
                    float(mu_eta[im]),
                    float(mu_phi[im]),
                ) >= 0.4:
                    continue
                if abs(float(jet_eta[j2]) - float(jet_eta[j3])) >= 1.6:
                    continue
                return True
        return False

    def trigger_doubletkelectron_puppiht_8_8_390(self, event):
        cut = self._cuts("DoubleTkElectron_PuppiHT_8_8_390")
        ht = self._puppi_ht(
            event, jet_pt_min=cut["ht_jet_pt_min"], eta_max=2.4
        )
        if ht < cut["ht"]:
            return False
        thresholds = (
            cut["leading_electron_pt"],
            cut["subleading_electron_pt"],
        )
        pt = self._field(event, self.ELECTRON, "PT")
        els = self._object_indices(
            event, self.ELECTRON, pt_min=min(thresholds), eta_max=2.4
        )
        return any(
            self._passes_thresholds((pt[i], pt[j]), thresholds)
            and self._dz_for_indices(event, self.ELECTRON, (i, j))
            for i, j in combinations(els, 2)
        )

    def trigger_doubletkmuon_puppiht_3_3_300(self, event):
        # LooseID is deliberately ignored.
        cut = self._cuts("DoubleTkMuon_PuppiHT_3_3_300")
        ht = self._puppi_ht(
            event, jet_pt_min=cut["ht_jet_pt_min"], eta_max=2.4
        )
        if ht < cut["ht"]:
            return False
        thresholds = (cut["leading_muon_pt"], cut["subleading_muon_pt"])
        pt = self._field(event, self.MUON, "PT")
        mus = self._object_indices(
            event, self.MUON, pt_min=min(thresholds), eta_max=2.4
        )
        return any(
            self._passes_thresholds((pt[i], pt[j]), thresholds)
            and self._dz_for_indices(event, self.MUON, (i, j))
            for i, j in combinations(mus, 2)
        )

    def trigger_doubletkmuon_puppijet_puppietmiss_3_3_60_130(self, event):
        # LooseID is deliberately ignored.
        cut = self._cuts("DoubleTkMuon_PuppiJet_PuppiETmiss_3_3_60_130")
        if self._scalar(event, "L1T_PUPPIMET_MET") < cut["met"]:
            return False
        if not self._jet_indices(event, pt_min=cut["jet_pt"], eta_max=2.4):
            return False
        thresholds = (cut["leading_muon_pt"], cut["subleading_muon_pt"])
        pt = self._field(event, self.MUON, "PT")
        mus = self._object_indices(
            event, self.MUON, pt_min=min(thresholds), eta_max=2.4
        )
        return any(
            self._passes_thresholds((pt[i], pt[j]), thresholds)
            and self._dz_for_indices(event, self.MUON, (i, j))
            for i, j in combinations(mus, 2)
        )

    def trigger_tkisoelectron_puppiht_26_190(self, event):
        # LooseNN is deliberately ignored.
        cut = self._cuts("TkIsoElectron_PuppiHT_26_190")
        ht = self._puppi_ht(
            event, jet_pt_min=cut["ht_jet_pt_min"], eta_max=2.4
        )
        if ht < cut["ht"]:
            return False
        return bool(
            self._isolated_indices(
                event,
                self.ELECTRON,
                pt_min=cut["electron_pt"],
                eta_max=2.4,
                iso_max=self.electron_iso_max,
            )
        )

    def trigger_tkelectron_puppijet_28_40(self, event):
        cut = self._cuts("TkElectron_PuppiJet_28_40")
        electron_eta = self._field(event, self.ELECTRON, "Eta")
        electron_phi = self._field(event, self.ELECTRON, "Phi")
        jet_eta = self._field(event, self.PUPPI_JET, "Eta")
        jet_phi = self._field(event, self.PUPPI_JET, "Phi")
        els = self._object_indices(
            event, self.ELECTRON, pt_min=cut["electron_pt"], eta_max=2.1
        )
        jets = self._jet_indices(event, pt_min=cut["jet_pt"], eta_max=2.4)
        return any(
            self._delta_r(
                float(electron_eta[ie]),
                float(electron_phi[ie]),
                float(jet_eta[ij]),
                float(jet_phi[ij]),
            )
            > 0.3
            for ie in els
            for ij in jets
        )

    def trigger_puppitau_puppietmiss_55_190(self, event):
        return self._raise_not_impl("PuppiTau_PuppiETmiss_55_190")

    def trigger_tkmuon_puppijet_puppietmiss_3_110_120(self, event):
        # LooseID is deliberately ignored.
        cut = self._cuts("TkMuon_PuppiJet_PuppiETmiss_3_110_120")
        if self._scalar(event, "L1T_PUPPIMET_MET") < cut["met"]:
            return False
        if not self._jet_indices(event, pt_min=cut["jet_pt"], eta_max=2.4):
            return False
        return bool(
            self._object_indices(
                event, self.MUON, pt_min=cut["muon_pt"], eta_max=2.4
            )
        )

    # ------------------------------------------------------------------
    # VBF rule
    # ------------------------------------------------------------------
    def trigger_vbf_doublepuppijets_160_35_mjj620(self, event):
        cut = self._cuts("VBF_DoublePuppiJets_160_35_mjj620")
        pt = self._field(event, self.PUPPI_JET, "PT")
        eta = self._field(event, self.PUPPI_JET, "Eta")
        phi = self._field(event, self.PUPPI_JET, "Phi")
        mass = self._field(event, self.PUPPI_JET, "Mass")
        thresholds = (cut["leading_jet_pt"], cut["subleading_jet_pt"])
        jets = self._jet_indices(event, pt_min=min(thresholds), eta_max=5.0)
        for i, j in combinations(jets, 2):
            if not self._passes_thresholds((pt[i], pt[j]), thresholds):
                continue
            mass_gev = self._pair_mass(
                float(pt[i]),
                float(eta[i]),
                float(phi[i]),
                float(mass[i]),
                float(pt[j]),
                float(eta[j]),
                float(phi[j]),
                float(mass[j]),
            )
            # The invariant-mass requirement is fixed, not threshold-calibrated.
            if mass_gev > 620.0:
                return True
        return False

    # ------------------------------------------------------------------
    # B-physics rules
    # ------------------------------------------------------------------
    def trigger_bph_doubletkmuon_2_2(self, event):
        cut = self._cuts("BPH_DoubleTkMuon_2_2")
        return self._bph_double_muon(
            event,
            pt_thresholds=(cut["leading_muon_pt"], cut["subleading_muon_pt"]),
            eta_max=1.5,
            dr_max=1.4,
        )

    def trigger_bph_doubletkmuon_4_4_dr1p5(self, event):
        # LooseID is deliberately ignored.
        cut = self._cuts("BPH_DoubleTkMuon_4_4_DR1p5")
        return self._bph_double_muon(
            event,
            pt_thresholds=(cut["leading_muon_pt"], cut["subleading_muon_pt"]),
            eta_max=2.4,
            dr_max=1.5,
        )

    def trigger_bph_doubletkmuon_4_4_m7_18(self, event):
        # LooseID is deliberately ignored; 7 < m(mu,mu) < 18 GeV is fixed.
        cut = self._cuts("BPH_DoubleTkMuon_4_4_m7_18")
        return self._bph_double_muon(
            event,
            pt_thresholds=(cut["leading_muon_pt"], cut["subleading_muon_pt"]),
            eta_max=2.0,
            mass_window=(7.0, 18.0),
        )

    def trigger_bph_tripletkmuon_5_3_0_m0_9(self, event):
        # LooseID is deliberately ignored; an OS pair must satisfy 0 < m < 9.
        cut = self._cuts("BPH_TripleTkMuon_5_3_0_m0_9")
        return self._bph_triple_muon(
            event,
            pt_thresholds=(
                cut["leading_muon_pt"],
                cut["second_muon_pt"],
                cut["third_muon_pt"],
            ),
            mass_window=(0.0, 9.0),
        )

    def trigger_bph_tripletkmuon_5_3_2_m5_17(self, event):
        # LooseID is deliberately ignored; an OS pair must satisfy 5 < m < 17.
        cut = self._cuts("BPH_TripleTkMuon_5_3_2_m5_17")
        return self._bph_triple_muon(
            event,
            pt_thresholds=(
                cut["leading_muon_pt"],
                cut["second_muon_pt"],
                cut["third_muon_pt"],
            ),
            mass_window=(5.0, 17.0),
        )


# Short alias for callers that prefer a menu-oriented name.
Collide2VTriggerMenuConfigurable = ConfigurableCollide2VTriggerMenu

# This matches the class name imported by the current application script.
TriggerMenuWithoutAbsolutePVDZ = ConfigurableCollide2VTriggerMenu

__all__ = [
    "ConfigurableCollide2VTriggerMenu",
    "Collide2VTriggerMenuConfigurable",
    "TriggerMenuWithoutAbsolutePVDZ",
]

