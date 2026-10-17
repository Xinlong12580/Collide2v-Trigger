"""Two PhotonTight objects with a mass in [120, 130] GeV; no extra ID/iso cuts.

Both photons must pass the same adjustable pT cut (default 1 GeV).
The mass window stays fixed during calibration. Every distinct pair is tested.
"""

from itertools import combinations
from math import cos, cosh, isfinite, sqrt

from collide2v_trigger import TriggerDefinition


def evaluate_diphoton(event, thresholds, fixed_parameters):
    pt = event["L1T_PhotonTight_PT"]
    eta = event["L1T_PhotonTight_Eta"]
    phi = event["L1T_PhotonTight_Phi"]
    if pt is None or eta is None or phi is None:
        return False
    if not len(pt) == len(eta) == len(phi):
        raise ValueError("Photon PT, Eta, and Phi arrays must have equal lengths")
    photons = [i for i in range(len(pt))
               if all(isfinite(x) for x in (pt[i], eta[i], phi[i]))
               and pt[i] >= thresholds["photon_pt"]]
    for i, j in combinations(photons, 2):
        # Massless photon four-vectors, with pT in GeV and phi in radians.
        mass2 = 2 * pt[i] * pt[j] * (cosh(eta[i] - eta[j]) - cos(phi[i] - phi[j]))
        mass = sqrt(max(0.0, mass2))
        if fixed_parameters["mass_min_gev"] <= mass <= fixed_parameters["mass_max_gev"]:
            return True
    return False


TRIGGER = TriggerDefinition(
    name="DoublePhoton_Mass120_130",
    evaluate=evaluate_diphoton,
    description="At least two PhotonTight objects, each above photon_pt, with 120 <= m(gamma gamma) <= 130 GeV",
    version="1",
    required_columns=("L1T_PhotonTight_PT", "L1T_PhotonTight_Eta", "L1T_PhotonTight_Phi"),
    default_thresholds={"photon_pt": 1.0},
    target_rate_khz=10.0,
    scale_fields=("photon_pt",),
    reference_field="photon_pt",
    fixed_parameters={"mass_min_gev": 120.0, "mass_max_gev": 130.0},
    monotonic=True,
)
