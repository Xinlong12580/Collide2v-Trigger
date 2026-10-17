"""COLLIDE-2V collection-based proxy of the supplied L1 trigger seed menu.

This version is built for the *full* L1T branch list supplied in the conversation.
It uses the dedicated reconstructed object collections rather than identifying
leptons/photons by PID in the PF/PUPPI candidate collection.

Main object mappings
--------------------
* tkMuon        -> L1T_MuonTight_*
* tkElectron    -> L1T_Electron_*
* tkIsoElectron -> L1T_Electron_* + IsolationVarRhoCorr (configurable WP)
* TkIsoPhoton   -> L1T_PhotonTight_* + IsolationVarRhoCorr (configurable WP)
* PuppiJet      -> L1T_JetPuppiAK4_*
* PuppiHT       -> L1T_ScalarHT_HT when the seed gives no constituent-jet cuts;
                   otherwise recomputed from L1T_JetPuppiAK4_* using the stated cuts
* PuppiETmiss   -> L1T_PUPPIMET_MET

The full branch list contains the important per-object quantities that were
missing from the shorter list:
* L1T_MuonTight_Charge and L1T_MuonTight_DZ
* L1T_Electron_Charge and L1T_Electron_DZ
* L1T_Electron_IsolationVar[ RhoCorr ]
* L1T_PhotonTight_IsolationVar[ RhoCorr ]

Therefore the collection-based implementation can evaluate Delta-z, BPH charge,
and electron/photon isolation requirements directly from the dedicated objects.

Unavailable objects
-------------------
No dedicated StaEG, CaloTau, or PuppiTau collections are present in the supplied
branches.  The corresponding trigger methods are retained as placeholders and
raise NotImplementedError.  With LooseID/LooseNN intentionally ignored, 33 of
the 42 menu seeds are implementable as proxies.

Important approximations
------------------------
1. LooseID and LooseNN are intentionally ignored, as requested.
2. The source table quotes offline thresholds; this proxy applies those values
   directly to COLLIDE-2V L1T quantities.
3. L1T_MuonTight_* is used as a proxy for the menu's generic tkMuon.  It may
   already impose a tighter selection than the actual tkMuon object.
4. L1T_PhotonTight_* is similarly only a proxy for the exact TkIsoPhoton object.
5. The exact CMS isolation working points are not specified by the menu.
   electron_iso_max and photon_iso_max default to 0.15 and are configurable.
6. For >=2 selected track objects, Delta-z is implemented as
      max(DZ) - min(DZ) < 1 cm.
   For a seed with one track object plus hadronic/event quantities, the proxy
   uses abs(DZ) < 1 cm, i.e. compatibility with the DZ reference/PV.
7. Multi-object requirements are evaluated over all candidate combinations,
   not only over leading objects.
8. For PuppiHT, seeds with explicit constituent-jet requirements are
   recomputed from L1T_JetPuppiAK4_*.  Seeds that only state an HT threshold
   use the stored L1T_ScalarHT_HT value.
9. Dimuon masses use the physical muon mass because MuonTight has no mass branch.

The class operates on a single-event mapping, as returned by unbatched
Hugging Face datasets.Dataset.filter / Dataset.map.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import atan2, cos, sin, sinh, sqrt
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class TriggerInfo:
    category: str
    display_name: str
    thresholds_gev: Tuple[float, ...]
    rate_khz: float
    requirements: str
    plateau_percent: Tuple[float, ...]
    note: str = ""


class Collide2VTriggerMenuObjectCollectionsFullBranches:
    """COLLIDE-2V trigger-menu proxy using reconstructed object collections."""

    MENU_RATE_KHZ = 417.0
    TOTAL_MENU_RATE_WITH_30PCT_KHZ = 542.0

    MUON = "L1T_MuonTight"
    ELECTRON = "L1T_Electron"
    PHOTON = "L1T_PhotonTight"
    PUPPI_JET = "L1T_JetPuppiAK4"

    MUON_MASS_GEV = 0.1056583755

    # Minimal branches actually consumed by the 33 implementable proxy seeds.
    # The full COLLIDE-2V dataset may contain many additional branches.
    BASE_REQUIRED_COLUMNS = (
        "L1T_MuonTight_PT",
        "L1T_MuonTight_Eta",
        "L1T_MuonTight_Phi",
        "L1T_MuonTight_Charge",
        "L1T_MuonTight_DZ",
        "L1T_Electron_PT",
        "L1T_Electron_Eta",
        "L1T_Electron_Phi",
        "L1T_Electron_DZ",
        "L1T_PhotonTight_PT",
        "L1T_PhotonTight_Eta",
        "L1T_PhotonTight_Phi",
        "L1T_JetPuppiAK4_PT",
        "L1T_JetPuppiAK4_Eta",
        "L1T_JetPuppiAK4_Phi",
        "L1T_JetPuppiAK4_Mass",
        "L1T_PUPPIMET_MET",
        "L1T_ScalarHT_HT",
    )


    TRIGGER_INFO: Dict[str, TriggerInfo] = {
        # Single/Double/Triple lepton seeds
        "SingleTkMuon_22": TriggerInfo("lepton", "Single tkMuon", (22,), 17, "|eta| < 2.4", (95,)),
        "DoubleTkMuon_15_7": TriggerInfo("lepton", "Double tkMuon", (15, 7), 2, "|eta| < 2.4, Delta z < 1", (95,)),
        "SingleTkElectron_36": TriggerInfo("lepton", "Single tkElectron", (36,), 28, "|eta| < 2.4", (93,)),
        "SingleTkIsoElectron_28": TriggerInfo("lepton", "Single tkIsoElectron", (28,), 37, "|eta| < 2.4", (93,), "Isolation is implemented with a configurable cut on the dedicated object IsolationVarRhoCorr branch."),
        "TkIsoElectron_StaEG_22_12": TriggerInfo("lepton", "TkIsoElectron-StaEG", (22, 12), 30, "|eta| < 2.4", (93, 99)),
        "DoubleTkElectron_25_12": TriggerInfo("lepton", "Double tkElectron", (25, 12), 14, "|eta| < 2.4, Delta z < 1", (93,)),
        "SingleStaEG_51": TriggerInfo("lepton", "Single StaEG", (51,), 32, "|eta| < 2.4", (99,)),
        "DoubleStaEG_37_24": TriggerInfo("lepton", "Double StaEG", (37, 24), 6, "|eta| < 2.4", (99,)),

        # Photon seeds
        "SingleTkIsoPhoton_36": TriggerInfo("photon", "Single TkIsoPhoton", (36,), 39, "|eta| < 2.4", (97,), "Isolation is implemented with a configurable cut on the dedicated object IsolationVarRhoCorr branch."),
        "DoubleTkIsoPhoton_22_12": TriggerInfo("photon", "Double TkIsoPhoton", (22, 12), 20, "|eta| < 2.4", (97,), "Isolation is implemented with a configurable cut on the dedicated object IsolationVarRhoCorr branch."),

        # Tau seeds
        "SingleCaloTau_150": TriggerInfo("tau", "Single CaloTau", (150,), 57, "|eta| < 2.172", (99,)),
        "DoubleCaloTau_90_90": TriggerInfo("tau", "Double CaloTau", (90, 90), 64, "|eta| < 2.172, Delta R > 0.5", (99,)),
        "DoublePuppiTau_52_52": TriggerInfo("tau", "Double PuppiTau", (52, 52), 27, "|eta| < 2.172, Delta R > 0.5, LooseNN", (90,)),

        # Hadronic seeds
        "SinglePuppiJet_230": TriggerInfo("hadronic", "Single PuppiJet", (230,), 37, "|eta| < 2.4", (100,)),
        "DoublePuppiJet_112_112": TriggerInfo("hadronic", "Double PuppiJet", (112, 112), 99, "|eta| < 2.4, Delta eta < 1.6", (100,)),
        "PuppiHT_450": TriggerInfo("hadronic", "PuppiHT", (450,), 10, "jets: |eta| < 2.4, pT > 30", (100,)),
        "PuppiHT_135": TriggerInfo("hadronic", "PuppiHT", (135,), 15, "jets: |eta| < 2.4, pT > 30", (100,)),
        "QuadPuppiJets_PuppiHT_400_70_55_40_40": TriggerInfo("hadronic", "QuadPuppiJets-PuppiHT (**) ", (400, 70, 55, 40, 40), 10, "jets: |eta| < 2.4, pT > 25", (100, 100)),

        # MET seeds
        "PuppiETmiss_200": TriggerInfo("met", "PuppiETmiss", (200,), 30, "", (100,)),

        # Cross lepton seeds
        "TkMuon_TkIsoElectron_7_20": TriggerInfo("cross_lepton", "TkMuon-TkIsoElectron", (7, 20), 0, "|eta| < 2.4, Delta z < 1", (95, 93)),
        "TkMuon_TkElectron_7_23": TriggerInfo("cross_lepton", "TkMuon-TkElectron", (7, 23), 2, "|eta| < 2.4, Delta z < 1", (95, 93)),
        "TkElectron_TkMuon_10_20": TriggerInfo("cross_lepton", "TkElectron-TkMuon", (10, 20), 2, "|eta| < 2.4, Delta z < 1", (93, 95)),
        "TripleTkMuon_5_3_3": TriggerInfo("cross_lepton", "Triple TkMuon", (5, 3, 3), 3, "|eta| < 2.4, Delta z < 1, LooseID", (95,)),
        "TkMuon_DoubleTkElectron_17_17_6": TriggerInfo("cross_lepton", "TkMuon-DoubleTkElectron", (17, 17, 6), 0, "|eta| < 2.4, Delta z < 1", (95, 93)),
        "DoubleTkMuon_TkElectron_9_5_5": TriggerInfo("cross_lepton", "DoubleTkMuon-TkElectron", (9, 5, 5), 2, "|eta| < 2.4, Delta z < 1, LooseID", (95, 93)),
        "PuppiTau_TkMuon_18_42": TriggerInfo("cross_lepton", "PuppiTau-TkMuon", (18, 42), 5, "|eta_tau| < 2.172, |eta_mu| < 2.1, Delta z < 1, LooseNN", (90, 95)),
        "TkIsoElectron_PuppiTau_22_45": TriggerInfo("cross_lepton", "TkIsoElectron-PuppiTau", (22, 45), 27, "|eta_tau| < 2.172, |eta_e| < 2.1, Delta z < 1, LooseNN", (93, 90)),

        # Cross hadronic-leptonic seeds
        "TkMuon_PuppiHT_6_320": TriggerInfo("cross_had_lep", "TkMuon-PuppiHT", (6, 320), 2, "|eta| < 2.4, Delta z < 1", (95, 100)),
        "TkMuon_TriplePuppiJet_12_40_40_40": TriggerInfo("cross_had_lep", "TkMuon-TriplePuppiJet", (12, 40, 40, 40), 7, "|eta| < 2.4, DeltaR(j1,mu) < 0.4, DeltaEta(j2,j3) < 1.6, Delta z < 1", (95, 100)),
        "DoubleTkElectron_PuppiHT_8_8_390": TriggerInfo("cross_had_lep", "DoubleTkElectron-PuppiHT", (8, 8, 390), 7, "|eta| < 2.4, Delta z < 1", (93, 100)),
        "DoubleTkMuon_PuppiHT_3_3_300": TriggerInfo("cross_had_lep", "DoubleTkMuon-PuppiHT", (3, 3, 300), 1, "|eta| < 2.4, Delta z < 1, LooseID", (93, 100)),
        "DoubleTkMuon_PuppiJet_PuppiETmiss_3_3_60_130": TriggerInfo("cross_had_lep", "DoubleTkMuon-PuppiJet-PuppiETmiss", (3, 3, 60, 130), 7, "|eta| < 2.4, Delta z < 1, LooseID", (95, 100, 100)),
        "TkIsoElectron_PuppiHT_26_190": TriggerInfo("cross_had_lep", "TkIsoElectron-PuppiHT", (26, 190), 13, "|eta| < 2.4, Delta z < 1, LooseNN", (93, 100)),
        "TkElectron_PuppiJet_28_40": TriggerInfo("cross_had_lep", "TkElectron-PuppiJet (**) ", (28, 40), 48, "|eta_e| < 2.1, |eta_j| < 2.4, Delta R > 0.3, Delta z < 1", (93, 100)),
        "PuppiTau_PuppiETmiss_55_190": TriggerInfo("cross_had_lep", "PuppiTau-PuppiETmiss", (55, 190), 10, "|eta| < 2.172, LooseNN", (90, 100)),
        "TkMuon_PuppiJet_PuppiETmiss_3_110_120": TriggerInfo("cross_had_lep", "TkMuon-PuppiJet-PuppiETmiss", (3, 110, 120), 57, "|eta| < 2.4, Delta z < 1, LooseID", (95, 100, 100)),

        # VBF
        "VBF_DoublePuppiJets_160_35_mjj620": TriggerInfo("vbf", "Double PuppiJets (**) ", (160, 35), 42, "|eta| < 5, mjj > 620", (100,)),

        # BPH
        "BPH_DoubleTkMuon_2_2": TriggerInfo("bph", "Double TkMuon", (2, 2), 13, "|eta| < 1.5, Delta z < 1, Delta R < 1.4, q1*q2 < 0", (95,)),
        "BPH_DoubleTkMuon_4_4_DR1p5": TriggerInfo("bph", "Double TkMuon", (4, 4), 7, "|eta| < 2.4, Delta z < 1, Delta R < 1.5, q1*q2 < 0, LooseID", (95,)),
        "BPH_DoubleTkMuon_4_4_m7_18": TriggerInfo("bph", "Double TkMuon", (4, 4), 3, "|eta| < 2.0, Delta z < 1, 7 < m < 18, q1*q2 < 0, LooseID", (95,)),
        "BPH_TripleTkMuon_5_3_0_m0_9": TriggerInfo("bph", "Triple TkMuon", (5, 3, 0), 1, "|eta| < 2.4, Delta z < 1, 0 < m < 9, q1*q2 < 0, LooseID", (95,)),
        "BPH_TripleTkMuon_5_3_2_m5_17": TriggerInfo("bph", "Triple TkMuon", (5, 3, 2), 0, "|eta| < 2.4, Delta z < 1, 5 < m < 17, q1*q2 < 0, LooseID", (95,)),
    }


    ALL_TRIGGERS: List[str] = list(TRIGGER_INFO.keys())

    UNIMPLEMENTABLE_TRIGGERS = {
        "TkIsoElectron_StaEG_22_12",
        "SingleStaEG_51",
        "DoubleStaEG_37_24",
        "SingleCaloTau_150",
        "DoubleCaloTau_90_90",
        "DoublePuppiTau_52_52",
        "PuppiTau_TkMuon_18_42",
        "TkIsoElectron_PuppiTau_22_45",
        "PuppiTau_PuppiETmiss_55_190",
    }

    NOT_IMPLEMENTABLE_REASONS: Dict[str, str] = {
        "TkIsoElectron_StaEG_22_12": "No dedicated StaEG collection is present in the supplied COLLIDE-2V columns.",
        "SingleStaEG_51": "No dedicated StaEG collection is present in the supplied COLLIDE-2V columns.",
        "DoubleStaEG_37_24": "No dedicated StaEG collection is present in the supplied COLLIDE-2V columns.",
        "SingleCaloTau_150": "No CaloTau/tau collection is present in the supplied COLLIDE-2V columns.",
        "DoubleCaloTau_90_90": "No CaloTau/tau collection is present in the supplied COLLIDE-2V columns.",
        "DoublePuppiTau_52_52": "No reconstructed PuppiTau collection is present. LooseNN is ignored, but the tau object itself is still missing.",
        "PuppiTau_TkMuon_18_42": "No reconstructed PuppiTau collection is present. LooseNN is ignored, but the tau object itself is still missing.",
        "TkIsoElectron_PuppiTau_22_45": "No reconstructed PuppiTau collection is present. LooseNN is ignored, but the tau object itself is still missing.",
        "PuppiTau_PuppiETmiss_55_190": "No reconstructed PuppiTau collection is present. LooseNN is ignored, but the tau object itself is still missing.",
    }

    OBJECT_MAPPING = {
        "tkMuon": "L1T_MuonTight_* (proxy; may be stricter than generic tkMuon)",
        "tkElectron": "L1T_Electron_*",
        "tkIsoElectron": "L1T_Electron_* + IsolationVarRhoCorr cut",
        "TkIsoPhoton": "L1T_PhotonTight_* + IsolationVarRhoCorr cut",
        "PuppiJet": "L1T_JetPuppiAK4_*",
        "PuppiHT": "L1T_ScalarHT_HT if no jet construction is stated; otherwise sum L1T_JetPuppiAK4_PT with the seed jet cuts",
        "PuppiETmiss": "L1T_PUPPIMET_MET",
        "StaEG": None,
        "CaloTau": None,
        "PuppiTau": None,
    }

    def __init__(
        self,
        *,
        electron_iso_max: float = 0.15,
        photon_iso_max: float = 0.15,
        use_rho_corrected_isolation: bool = True,
    ) -> None:
        self.electron_iso_max = float(electron_iso_max)
        self.photon_iso_max = float(photon_iso_max)
        self.use_rho_corrected_isolation = bool(use_rho_corrected_isolation)

        self.all_triggers = list(self.ALL_TRIGGERS)
        self.unimplementable_triggers = [
            t for t in self.all_triggers if t in self.UNIMPLEMENTABLE_TRIGGERS
        ]
        self.implementable_triggers = [
            t for t in self.all_triggers if t not in self.UNIMPLEMENTABLE_TRIGGERS
        ]
        self.trigger_info = dict(self.TRIGGER_INFO)
        self.menu_rate_khz = self.MENU_RATE_KHZ
        self.total_menu_rate_with_30pct_khz = self.TOTAL_MENU_RATE_WITH_30PCT_KHZ
        self.object_mapping = dict(self.OBJECT_MAPPING)
        self.ignored_requirements = ("LooseID", "LooseNN")

        # Helpful fidelity labels for downstream studies.
        self.proxy_triggers = [
            t for t in self.implementable_triggers
            if any(x in self.trigger_info[t].display_name for x in ("Muon", "Electron", "Photon"))
        ]

        self._dispatch: Dict[str, Callable[[Mapping[str, Any]], bool]] = {
            name: getattr(self, self._method_name(name)) for name in self.all_triggers
        }

    @property
    def required_columns(self) -> List[str]:
        """Columns needed by all currently implementable seeds."""
        cols = list(self.BASE_REQUIRED_COLUMNS)
        if self.use_rho_corrected_isolation:
            cols.extend([
                "L1T_Electron_IsolationVarRhoCorr",
                "L1T_PhotonTight_IsolationVarRhoCorr",
            ])
        else:
            cols.extend([
                "L1T_Electron_IsolationVar",
                "L1T_PhotonTight_IsolationVar",
            ])
        return sorted(set(cols))

    def missing_required_columns(self, available_columns: Sequence[str]) -> List[str]:
        """Return required columns absent from a dataset schema."""
        available = set(available_columns)
        return [c for c in self.required_columns if c not in available]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @staticmethod
    def _method_name(trigger_name: str) -> str:
        return "trigger_" + trigger_name.lower()

    def evaluate(self, trigger_name: str, event: Mapping[str, Any]) -> bool:
        if trigger_name not in self._dispatch:
            raise KeyError(f"Unknown trigger: {trigger_name}")
        return bool(self._dispatch[trigger_name](event))

    def passes_any(
        self,
        event: Mapping[str, Any],
        triggers: Optional[Sequence[str]] = None,
    ) -> bool:
        names = self.implementable_triggers if triggers is None else list(triggers)
        return any(self.evaluate(name, event) for name in names)

    def trigger_bits(
        self,
        event: Mapping[str, Any],
        *,
        include_unimplemented: bool = False,
    ) -> Dict[str, bool]:
        names = self.all_triggers if include_unimplemented else self.implementable_triggers
        return {name: self.evaluate(name, event) for name in names}

    def menu_summary(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for name in self.all_triggers:
            info = self.trigger_info[name]
            ignored = tuple(req for req in self.ignored_requirements if req in info.requirements)
            rows.append({
                "name": name,
                "category": info.category,
                "display_name": info.display_name,
                "thresholds_gev": info.thresholds_gev,
                "rate_khz": info.rate_khz,
                "requirements": info.requirements,
                "plateau_percent": info.plateau_percent,
                "implementable": name in self.implementable_triggers,
                "ignored_requirements": ignored,
                "reason_if_not_implementable": self.NOT_IMPLEMENTABLE_REASONS.get(name, ""),
                "note": info.note,
            })
        return rows

    # ------------------------------------------------------------------
    # Generic data helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _array(event: Mapping[str, Any], key: str, dtype=float) -> np.ndarray:
        if key not in event:
            raise KeyError(f"Required COLLIDE-2V column '{key}' is missing from the event.")
        value = event[key]
        if value is None:
            return np.asarray([], dtype=dtype)
        return np.asarray(value, dtype=dtype).reshape(-1)

    @classmethod
    def _field(cls, event: Mapping[str, Any], prefix: str, field: str, dtype=float) -> np.ndarray:
        return cls._array(event, f"{prefix}_{field}", dtype=dtype)

    @classmethod
    def _scalar(cls, event: Mapping[str, Any], key: str) -> float:
        arr = cls._array(event, key, dtype=float)
        return float(arr[0]) if len(arr) else float("nan")

    @staticmethod
    def _delta_phi(phi1: float, phi2: float) -> float:
        return atan2(sin(phi1 - phi2), cos(phi1 - phi2))

    @classmethod
    def _delta_r(cls, eta1: float, phi1: float, eta2: float, phi2: float) -> float:
        return sqrt((eta1 - eta2) ** 2 + cls._delta_phi(phi1, phi2) ** 2)

    @staticmethod
    def _passes_thresholds(pt_values: Sequence[float], thresholds: Sequence[float]) -> bool:
        pts = sorted((float(x) for x in pt_values), reverse=True)
        thrs = sorted((float(x) for x in thresholds), reverse=True)
        return len(pts) >= len(thrs) and all(p >= t for p, t in zip(pts, thrs))

    @staticmethod
    def _pair_mass(
        pt1: float, eta1: float, phi1: float, m1: float,
        pt2: float, eta2: float, phi2: float, m2: float,
    ) -> float:
        px1, py1, pz1 = pt1 * cos(phi1), pt1 * sin(phi1), pt1 * sinh(eta1)
        px2, py2, pz2 = pt2 * cos(phi2), pt2 * sin(phi2), pt2 * sinh(eta2)
        e1 = sqrt(max(px1 * px1 + py1 * py1 + pz1 * pz1 + m1 * m1, 0.0))
        e2 = sqrt(max(px2 * px2 + py2 * py2 + pz2 * pz2 + m2 * m2, 0.0))
        e = e1 + e2
        px, py, pz = px1 + px2, py1 + py2, pz1 + pz2
        return sqrt(max(e * e - px * px - py * py - pz * pz, 0.0))

    @classmethod
    def _object_indices(
        cls,
        event: Mapping[str, Any],
        prefix: str,
        *,
        pt_min: float = 0.0,
        eta_max: Optional[float] = None,
    ) -> List[int]:
        pt = cls._field(event, prefix, "PT")
        eta = cls._field(event, prefix, "Eta")
        n = min(len(pt), len(eta))
        return [
            i for i in range(n)
            if float(pt[i]) >= pt_min
            and (eta_max is None or abs(float(eta[i])) < eta_max)
        ]

    @staticmethod
    def _dz_compatible_values(values: Sequence[float], max_dz: float = 1.0) -> bool:
        vals = [float(x) for x in values]
        if not vals:
            return True
        if len(vals) == 1:
            return abs(vals[0]) < max_dz
        return max(vals) - min(vals) < max_dz

    @classmethod
    def _dz_for_indices(
        cls,
        event: Mapping[str, Any],
        prefix: str,
        indices: Sequence[int],
        max_dz: float = 1.0,
    ) -> bool:
        dz = cls._field(event, prefix, "DZ")
        if any(i >= len(dz) for i in indices):
            return False
        return cls._dz_compatible_values([dz[i] for i in indices], max_dz=max_dz)

    @classmethod
    def _mixed_dz_compatible(
        cls,
        event: Mapping[str, Any],
        objects: Sequence[Tuple[str, int]],
        max_dz: float = 1.0,
    ) -> bool:
        values: List[float] = []
        for prefix, idx in objects:
            dz = cls._field(event, prefix, "DZ")
            if idx >= len(dz):
                return False
            values.append(float(dz[idx]))
        return cls._dz_compatible_values(values, max_dz=max_dz)

    def _isolated_indices(
        self,
        event: Mapping[str, Any],
        prefix: str,
        *,
        pt_min: float,
        eta_max: float,
        iso_max: float,
    ) -> List[int]:
        base = self._object_indices(event, prefix, pt_min=pt_min, eta_max=eta_max)
        field = "IsolationVarRhoCorr" if self.use_rho_corrected_isolation else "IsolationVar"
        iso = self._field(event, prefix, field)
        return [i for i in base if i < len(iso) and np.isfinite(iso[i]) and float(iso[i]) <= iso_max]

    @classmethod
    def _jet_indices(cls, event: Mapping[str, Any], *, pt_min: float, eta_max: float) -> List[int]:
        return cls._object_indices(event, cls.PUPPI_JET, pt_min=pt_min, eta_max=eta_max)

    @classmethod
    def _puppi_ht(cls, event: Mapping[str, Any], *, jet_pt_min: float, eta_max: float) -> float:
        pt = cls._field(event, cls.PUPPI_JET, "PT")
        eta = cls._field(event, cls.PUPPI_JET, "Eta")
        n = min(len(pt), len(eta))
        return float(sum(float(pt[i]) for i in range(n) if float(pt[i]) > jet_pt_min and abs(float(eta[i])) < eta_max))

    def _raise_not_impl(self, name: str) -> bool:
        reason = self.NOT_IMPLEMENTABLE_REASONS.get(name, "Required trigger object is unavailable.")
        raise NotImplementedError(f"{name}: {reason}")

    # ------------------------------------------------------------------
    # Single / double lepton
    # ------------------------------------------------------------------
    def trigger_singletkmuon_22(self, event):
        return bool(self._object_indices(event, self.MUON, pt_min=22, eta_max=2.4))

    def trigger_doubletkmuon_15_7(self, event):
        pt = self._field(event, self.MUON, "PT")
        mus = self._object_indices(event, self.MUON, pt_min=7, eta_max=2.4)
        return any(
            self._passes_thresholds((pt[i], pt[j]), (15, 7))
            and self._dz_for_indices(event, self.MUON, (i, j))
            for i, j in combinations(mus, 2)
        )

    def trigger_singletkelectron_36(self, event):
        return bool(self._object_indices(event, self.ELECTRON, pt_min=36, eta_max=2.4))

    def trigger_singletkisoelectron_28(self, event):
        return bool(self._isolated_indices(event, self.ELECTRON, pt_min=28, eta_max=2.4, iso_max=self.electron_iso_max))

    def trigger_tkisoelectron_staeg_22_12(self, event):
        return self._raise_not_impl("TkIsoElectron_StaEG_22_12")

    def trigger_doubletkelectron_25_12(self, event):
        pt = self._field(event, self.ELECTRON, "PT")
        els = self._object_indices(event, self.ELECTRON, pt_min=12, eta_max=2.4)
        return any(
            self._passes_thresholds((pt[i], pt[j]), (25, 12))
            and self._dz_for_indices(event, self.ELECTRON, (i, j))
            for i, j in combinations(els, 2)
        )

    def trigger_singlestaeg_51(self, event):
        return self._raise_not_impl("SingleStaEG_51")

    def trigger_doublestaeg_37_24(self, event):
        return self._raise_not_impl("DoubleStaEG_37_24")

    # ------------------------------------------------------------------
    # Photon
    # ------------------------------------------------------------------
    def trigger_singletkisophoton_36(self, event):
        return bool(self._isolated_indices(event, self.PHOTON, pt_min=36, eta_max=2.4, iso_max=self.photon_iso_max))

    def trigger_doubletkisophoton_22_12(self, event):
        pt = self._field(event, self.PHOTON, "PT")
        phs = self._isolated_indices(event, self.PHOTON, pt_min=12, eta_max=2.4, iso_max=self.photon_iso_max)
        return any(self._passes_thresholds((pt[i], pt[j]), (22, 12)) for i, j in combinations(phs, 2))

    # ------------------------------------------------------------------
    # Tau placeholders
    # ------------------------------------------------------------------
    def trigger_singlecalotau_150(self, event):
        return self._raise_not_impl("SingleCaloTau_150")

    def trigger_doublecalotau_90_90(self, event):
        return self._raise_not_impl("DoubleCaloTau_90_90")

    def trigger_doublepuppitau_52_52(self, event):
        return self._raise_not_impl("DoublePuppiTau_52_52")

    # ------------------------------------------------------------------
    # Hadronic
    # ------------------------------------------------------------------
    def trigger_singlepuppijet_230(self, event):
        return bool(self._jet_indices(event, pt_min=230, eta_max=2.4))

    def trigger_doublepuppijet_112_112(self, event):
        eta = self._field(event, self.PUPPI_JET, "Eta")
        jets = self._jet_indices(event, pt_min=112, eta_max=2.4)
        return any(abs(float(eta[i]) - float(eta[j])) < 1.6 for i, j in combinations(jets, 2))

    def trigger_puppiht_450(self, event):
        return self._puppi_ht(event, jet_pt_min=30, eta_max=2.4) >= 450

    def trigger_puppiht_135(self, event):
        return self._puppi_ht(event, jet_pt_min=30, eta_max=2.4) >= 135

    def trigger_quadpuppijets_puppiht_400_70_55_40_40(self, event):
        pt = self._field(event, self.PUPPI_JET, "PT")
        eta = self._field(event, self.PUPPI_JET, "Eta")
        n = min(len(pt), len(eta))
        eligible = [i for i in range(n) if float(pt[i]) > 25 and abs(float(eta[i])) < 2.4]
        if sum(float(pt[i]) for i in eligible) < 400:
            return False
        return any(self._passes_thresholds([pt[i] for i in combo], (70, 55, 40, 40)) for combo in combinations(eligible, 4))

    # ------------------------------------------------------------------
    # MET
    # ------------------------------------------------------------------
    def trigger_puppietmiss_200(self, event):
        return self._scalar(event, "L1T_PUPPIMET_MET") >= 200

    # ------------------------------------------------------------------
    # Cross lepton
    # ------------------------------------------------------------------
    def _mu_e_pair(self, event, mu_pt: float, e_pt: float, *, iso_e: bool = False, eta_max: float = 2.4) -> bool:
        mus = self._object_indices(event, self.MUON, pt_min=mu_pt, eta_max=eta_max)
        els = (
            self._isolated_indices(event, self.ELECTRON, pt_min=e_pt, eta_max=eta_max, iso_max=self.electron_iso_max)
            if iso_e else
            self._object_indices(event, self.ELECTRON, pt_min=e_pt, eta_max=eta_max)
        )
        return any(self._mixed_dz_compatible(event, ((self.MUON, im), (self.ELECTRON, ie))) for im in mus for ie in els)

    def trigger_tkmuon_tkisoelectron_7_20(self, event):
        return self._mu_e_pair(event, 7, 20, iso_e=True)

    def trigger_tkmuon_tkelectron_7_23(self, event):
        return self._mu_e_pair(event, 7, 23)

    def trigger_tkelectron_tkmuon_10_20(self, event):
        return self._mu_e_pair(event, 20, 10)

    def trigger_tripletkmuon_5_3_3(self, event):
        # LooseID deliberately ignored.
        pt = self._field(event, self.MUON, "PT")
        mus = self._object_indices(event, self.MUON, pt_min=3, eta_max=2.4)
        return any(
            self._passes_thresholds([pt[i] for i in combo], (5, 3, 3))
            and self._dz_for_indices(event, self.MUON, combo)
            for combo in combinations(mus, 3)
        )

    def trigger_tkmuon_doubletkelectron_17_17_6(self, event):
        ept = self._field(event, self.ELECTRON, "PT")
        mus = self._object_indices(event, self.MUON, pt_min=17, eta_max=2.4)
        els = self._object_indices(event, self.ELECTRON, pt_min=6, eta_max=2.4)
        for im in mus:
            for i, j in combinations(els, 2):
                if not self._passes_thresholds((ept[i], ept[j]), (17, 6)):
                    continue
                if self._mixed_dz_compatible(event, ((self.MUON, im), (self.ELECTRON, i), (self.ELECTRON, j))):
                    return True
        return False

    def trigger_doubletkmuon_tkelectron_9_5_5(self, event):
        # LooseID deliberately ignored. Interpreted as muon legs (9,5), electron leg 5.
        mpt = self._field(event, self.MUON, "PT")
        mus = self._object_indices(event, self.MUON, pt_min=5, eta_max=2.4)
        els = self._object_indices(event, self.ELECTRON, pt_min=5, eta_max=2.4)
        for i, j in combinations(mus, 2):
            if not self._passes_thresholds((mpt[i], mpt[j]), (9, 5)):
                continue
            for ie in els:
                if self._mixed_dz_compatible(event, ((self.MUON, i), (self.MUON, j), (self.ELECTRON, ie))):
                    return True
        return False

    def trigger_puppitau_tkmuon_18_42(self, event):
        return self._raise_not_impl("PuppiTau_TkMuon_18_42")

    def trigger_tkisoelectron_puppitau_22_45(self, event):
        return self._raise_not_impl("TkIsoElectron_PuppiTau_22_45")

    # ------------------------------------------------------------------
    # Cross hadronic-leptonic
    # ------------------------------------------------------------------
    def trigger_tkmuon_puppiht_6_320(self, event):
        if self._scalar(event, "L1T_ScalarHT_HT") < 320:
            return False
        mus = self._object_indices(event, self.MUON, pt_min=6, eta_max=2.4)
        return any(self._dz_for_indices(event, self.MUON, (i,)) for i in mus)

    def trigger_tkmuon_triplepuppijet_12_40_40_40(self, event):
        mu_eta = self._field(event, self.MUON, "Eta")
        mu_phi = self._field(event, self.MUON, "Phi")
        jpt = self._field(event, self.PUPPI_JET, "PT")
        jeta = self._field(event, self.PUPPI_JET, "Eta")
        jphi = self._field(event, self.PUPPI_JET, "Phi")
        mus = self._object_indices(event, self.MUON, pt_min=12, eta_max=2.4)
        jets = self._jet_indices(event, pt_min=40, eta_max=2.4)
        for im in mus:
            if not self._dz_for_indices(event, self.MUON, (im,)):
                continue
            for combo in combinations(jets, 3):
                j1, j2, j3 = sorted(combo, key=lambda k: float(jpt[k]), reverse=True)
                if self._delta_r(float(jeta[j1]), float(jphi[j1]), float(mu_eta[im]), float(mu_phi[im])) >= 0.4:
                    continue
                if abs(float(jeta[j2]) - float(jeta[j3])) >= 1.6:
                    continue
                return True
        return False

    def trigger_doubletkelectron_puppiht_8_8_390(self, event):
        if self._scalar(event, "L1T_ScalarHT_HT") < 390:
            return False
        els = self._object_indices(event, self.ELECTRON, pt_min=8, eta_max=2.4)
        return any(self._dz_for_indices(event, self.ELECTRON, pair) for pair in combinations(els, 2))

    def trigger_doubletkmuon_puppiht_3_3_300(self, event):
        # LooseID deliberately ignored.
        if self._scalar(event, "L1T_ScalarHT_HT") < 300:
            return False
        mus = self._object_indices(event, self.MUON, pt_min=3, eta_max=2.4)
        return any(self._dz_for_indices(event, self.MUON, pair) for pair in combinations(mus, 2))

    def trigger_doubletkmuon_puppijet_puppietmiss_3_3_60_130(self, event):
        # LooseID deliberately ignored.
        if self._scalar(event, "L1T_PUPPIMET_MET") < 130:
            return False
        if not self._jet_indices(event, pt_min=60, eta_max=2.4):
            return False
        mus = self._object_indices(event, self.MUON, pt_min=3, eta_max=2.4)
        return any(self._dz_for_indices(event, self.MUON, pair) for pair in combinations(mus, 2))

    def trigger_tkisoelectron_puppiht_26_190(self, event):
        # LooseNN in the table is deliberately ignored.
        if self._scalar(event, "L1T_ScalarHT_HT") < 190:
            return False
        els = self._isolated_indices(event, self.ELECTRON, pt_min=26, eta_max=2.4, iso_max=self.electron_iso_max)
        return any(self._dz_for_indices(event, self.ELECTRON, (i,)) for i in els)

    def trigger_tkelectron_puppijet_28_40(self, event):
        eeta = self._field(event, self.ELECTRON, "Eta")
        ephi = self._field(event, self.ELECTRON, "Phi")
        jeta = self._field(event, self.PUPPI_JET, "Eta")
        jphi = self._field(event, self.PUPPI_JET, "Phi")
        els = self._object_indices(event, self.ELECTRON, pt_min=28, eta_max=2.1)
        jets = self._jet_indices(event, pt_min=40, eta_max=2.4)
        for ie in els:
            if not self._dz_for_indices(event, self.ELECTRON, (ie,)):
                continue
            for ij in jets:
                if self._delta_r(float(eeta[ie]), float(ephi[ie]), float(jeta[ij]), float(jphi[ij])) > 0.3:
                    return True
        return False

    def trigger_puppitau_puppietmiss_55_190(self, event):
        return self._raise_not_impl("PuppiTau_PuppiETmiss_55_190")

    def trigger_tkmuon_puppijet_puppietmiss_3_110_120(self, event):
        # LooseID deliberately ignored.
        if self._scalar(event, "L1T_PUPPIMET_MET") < 120:
            return False
        if not self._jet_indices(event, pt_min=110, eta_max=2.4):
            return False
        mus = self._object_indices(event, self.MUON, pt_min=3, eta_max=2.4)
        return any(self._dz_for_indices(event, self.MUON, (i,)) for i in mus)

    # ------------------------------------------------------------------
    # VBF
    # ------------------------------------------------------------------
    def trigger_vbf_doublepuppijets_160_35_mjj620(self, event):
        pt = self._field(event, self.PUPPI_JET, "PT")
        eta = self._field(event, self.PUPPI_JET, "Eta")
        phi = self._field(event, self.PUPPI_JET, "Phi")
        mass = self._field(event, self.PUPPI_JET, "Mass")
        jets = self._jet_indices(event, pt_min=35, eta_max=5.0)
        for i, j in combinations(jets, 2):
            if not self._passes_thresholds((pt[i], pt[j]), (160, 35)):
                continue
            mjj = self._pair_mass(
                float(pt[i]), float(eta[i]), float(phi[i]), float(mass[i]),
                float(pt[j]), float(eta[j]), float(phi[j]), float(mass[j]),
            )
            if mjj > 620:
                return True
        return False

    # ------------------------------------------------------------------
    # BPH
    # ------------------------------------------------------------------
    def _bph_double_muon(
        self,
        event: Mapping[str, Any],
        *,
        pt_thresholds: Tuple[float, float],
        eta_max: float,
        dr_max: Optional[float] = None,
        mass_window: Optional[Tuple[float, float]] = None,
    ) -> bool:
        pt = self._field(event, self.MUON, "PT")
        eta = self._field(event, self.MUON, "Eta")
        phi = self._field(event, self.MUON, "Phi")
        charge = self._field(event, self.MUON, "Charge")
        mus = self._object_indices(event, self.MUON, pt_min=min(pt_thresholds), eta_max=eta_max)
        for i, j in combinations(mus, 2):
            if not self._passes_thresholds((pt[i], pt[j]), pt_thresholds):
                continue
            if float(charge[i]) * float(charge[j]) >= 0:
                continue
            if not self._dz_for_indices(event, self.MUON, (i, j)):
                continue
            if dr_max is not None and self._delta_r(float(eta[i]), float(phi[i]), float(eta[j]), float(phi[j])) >= dr_max:
                continue
            if mass_window is not None:
                mll = self._pair_mass(
                    float(pt[i]), float(eta[i]), float(phi[i]), self.MUON_MASS_GEV,
                    float(pt[j]), float(eta[j]), float(phi[j]), self.MUON_MASS_GEV,
                )
                if not (mass_window[0] < mll < mass_window[1]):
                    continue
            return True
        return False

    def trigger_bph_doubletkmuon_2_2(self, event):
        return self._bph_double_muon(event, pt_thresholds=(2, 2), eta_max=1.5, dr_max=1.4)

    def trigger_bph_doubletkmuon_4_4_dr1p5(self, event):
        # LooseID deliberately ignored.
        return self._bph_double_muon(event, pt_thresholds=(4, 4), eta_max=2.4, dr_max=1.5)

    def trigger_bph_doubletkmuon_4_4_m7_18(self, event):
        # LooseID deliberately ignored.
        return self._bph_double_muon(event, pt_thresholds=(4, 4), eta_max=2.0, mass_window=(7, 18))

    def _bph_triple_muon(
        self,
        event: Mapping[str, Any],
        *,
        pt_thresholds: Tuple[float, float, float],
        mass_window: Tuple[float, float],
        eta_max: float = 2.4,
    ) -> bool:
        pt = self._field(event, self.MUON, "PT")
        eta = self._field(event, self.MUON, "Eta")
        phi = self._field(event, self.MUON, "Phi")
        charge = self._field(event, self.MUON, "Charge")
        mus = self._object_indices(event, self.MUON, pt_min=min(pt_thresholds), eta_max=eta_max)
        for combo in combinations(mus, 3):
            if not self._passes_thresholds([pt[i] for i in combo], pt_thresholds):
                continue
            if not self._dz_for_indices(event, self.MUON, combo):
                continue
            # q1*q2<0 + mass window is interpreted as requiring at least one
            # opposite-sign pair inside the selected triplet satisfying the mass cut.
            for i, j in combinations(combo, 2):
                if float(charge[i]) * float(charge[j]) >= 0:
                    continue
                mll = self._pair_mass(
                    float(pt[i]), float(eta[i]), float(phi[i]), self.MUON_MASS_GEV,
                    float(pt[j]), float(eta[j]), float(phi[j]), self.MUON_MASS_GEV,
                )
                if mass_window[0] < mll < mass_window[1]:
                    return True
        return False

    def trigger_bph_tripletkmuon_5_3_0_m0_9(self, event):
        # LooseID deliberately ignored.
        return self._bph_triple_muon(event, pt_thresholds=(5, 3, 0), mass_window=(0, 9))

    def trigger_bph_tripletkmuon_5_3_2_m5_17(self, event):
        # LooseID deliberately ignored.
        return self._bph_triple_muon(event, pt_thresholds=(5, 3, 2), mass_window=(5, 17))


# Drop-in alias matching the PID-based class name used in the earlier version.
Collide2VTriggerMenuIgnoreIDNN = Collide2VTriggerMenuObjectCollectionsFullBranches
Collide2VTriggerMenuObjectCollectionsIgnoreIDNN = Collide2VTriggerMenuObjectCollectionsFullBranches


if __name__ == "__main__":
    # Example with Hugging Face datasets:
    #
    # menu = Collide2VTriggerMenuObjectCollectionsFullBranches()
    # print(len(menu.all_triggers))            # 42
    # print(len(menu.implementable_triggers))  # 33
    #
    # selected = dataset.filter(menu.passes_any)
    # selected_mu = dataset.filter(menu.trigger_singletkmuon_22)
    # dataset_with_bits = dataset.map(lambda event: menu.trigger_bits(event))
    pass
