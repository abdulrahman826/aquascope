"""Diagnosis confirmation states (M2). A confident model prediction is NOT a confirmed fault.

  CONFIDENT_BUT_UNVERIFIED    the model's validated threshold was met; nobody has checked it
  CONFIRMED_BY_INVESTIGATION  a simulated field reading resolved the competing explanations (policy below)
  OPERATOR_CONFIRMED          a named operator asserted the fault (recorded verbatim, not verified by AquaScope)
  INSUFFICIENT_EVIDENCE       ambiguous / abstained diagnosis, or an investigation that did not resolve it

Only the two CONFIRMED states unlock hydraulic interventions (remediation.config.HYDRAULIC_UNLOCK_STATUSES).
Builders only read outputs of the existing diagnosis / investigation engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .. import config as C
from ..investigation import MIN_SEPARATION
from ..simulation import valid_locations
from . import config as R

Status = Literal["CONFIDENT_BUT_UNVERIFIED", "CONFIRMED_BY_INVESTIGATION", "OPERATOR_CONFIRMED", "INSUFFICIENT_EVIDENCE"]


@dataclass(frozen=True)
class ConfirmedDiagnosis:
    status: Status
    event_class: Optional[str]
    location: Optional[str]
    probability: Optional[float]
    source: str
    reason: str
    parameters: tuple = ()                      # fitted physical magnitude, (name, value) pairs, if known
    alternative_locations: tuple = ()           # other candidate locations from the physics localisation
    evidence: tuple = field(default=(), compare=False)

    @property
    def unlocks_hydraulic(self) -> bool:
        return self.status in R.HYDRAULIC_UNLOCK_STATUSES


def _loc_key(cls: str) -> str:
    return "leak" if cls in C.LEAK_CLASSES else ("sensor" if cls in C.SENSOR_CLASSES else cls)


def _alternatives(diag: dict, cls: str, loc: Optional[str]) -> tuple:
    fits = diag.get("physics", {}).get("localization", {}).get(_loc_key(cls), [])
    return tuple(f["location"] for f in fits[:3] if f["location"] != loc)


def from_diagnosis(diag: dict) -> ConfirmedDiagnosis:
    """Model output only -> CONFIDENT_BUT_UNVERIFIED or INSUFFICIENT_EVIDENCE (never confirmed)."""
    lead = diag["hypotheses"][0]
    if diag["status"] != "CONFIDENT":
        return ConfirmedDiagnosis("INSUFFICIENT_EVIDENCE", None, None, None, "diagnosis",
                                  f"diagnosis status {diag['status']}: {diag['reason']}")
    loc = lead["candidate_locations"][0] if lead["candidate_locations"] else None
    return ConfirmedDiagnosis("CONFIDENT_BUT_UNVERIFIED", lead["event_class"], loc, lead["probability"], "diagnosis",
                              f"{diag['approach']} probability {lead['probability']:.2f} >= validated threshold "
                              f"{diag['threshold']:.2f}; not verified by measurement or operator",
                              alternative_locations=_alternatives(diag, lead["event_class"], loc))


def from_investigation(diag: dict, rec: dict, meas: dict, kway_posterior: Optional[float] = None) -> ConfirmedDiagnosis:
    """CONFIRMED_BY_INVESTIGATION only if the reading is consistent, informative and decisive, AND it still wins
    against the K leading physical explanations (kway_posterior, see verification.kway_posterior). Without the K-way
    check the measurement cannot confirm. Use confirm_measurement() to run everything."""
    cand = next((c for c in rec.get("candidates", []) if c["node"] == meas["node"]), None)
    winner = max(meas["posterior"], key=lambda p: p["probability"])
    problems = []
    if cand is None:
        problems.append(f"measured node {meas['node']} is not a candidate of this recommendation")
    elif cand["separation"] < MIN_SEPARATION:
        problems.append(f"node {meas['node']} separates the hypotheses by only {cand['separation']:.2f} sigma "
                        f"(< {MIN_SEPARATION})")
    if not meas["consistent_with_a_hypothesis"]:
        problems.append("reading is > 3 sigma from both predictions (neither explanation fits)")
    wi = max(range(len(meas["posterior"])), key=lambda i: meas["posterior"][i]["probability"])
    if abs(meas["z_scores"][wi]) > R.CONFIRMATION_MAX_Z:
        problems.append(f"reading is {abs(meas['z_scores'][wi]):.2f} sigma from the winning prediction "
                        f"(> {R.CONFIRMATION_MAX_Z})")
    if winner["probability"] < R.CONFIRMATION_MIN_POSTERIOR:
        problems.append(f"posterior {winner['probability']:.3f} < {R.CONFIRMATION_MIN_POSTERIOR}")
    if kway_posterior is None:
        problems.append("multi-hypothesis (K-way) check not run")
    elif kway_posterior < R.CONFIRMATION_MIN_KWAY_POSTERIOR:
        problems.append(f"another leading explanation fits the reading comparably (K-way posterior "
                        f"{kway_posterior:.3f} < {R.CONFIRMATION_MIN_KWAY_POSTERIOR})")
    ev = (f"reading {meas['reading_m']} m at junction {meas['node']}", f"z-scores {meas['z_scores']}",
          f"posterior {winner['probability']:.3f} for '{winner['description']}'",
          f"K-way posterior {kway_posterior if kway_posterior is None else round(kway_posterior, 3)}")
    if problems:
        return ConfirmedDiagnosis("INSUFFICIENT_EVIDENCE", None, None, None, "investigation",
                                  "investigation did not confirm: " + "; ".join(problems), evidence=ev)
    cls, loc = meas["resolved_event_class"], meas["resolved_location"]
    hyp = next((h for h in rec["hypotheses"] if h["event_class"] == cls and h["location"] == loc), None)
    params = tuple(sorted((hyp or {}).get("fitted_parameters", {}).items()))
    return ConfirmedDiagnosis("CONFIRMED_BY_INVESTIGATION", cls, loc, winner["probability"], "investigation",
                              f"simulated reading at junction {meas['node']} resolved '{winner['description']}' "
                              f"(posterior {winner['probability']:.3f}, separation {cand['separation']:.2f} sigma)",
                              parameters=params, alternative_locations=_alternatives(diag, cls, loc), evidence=ev)


def confirm_measurement(sc: dict, diag: dict, rec: dict, meas: dict) -> ConfirmedDiagnosis:
    """Full confirmation decision for a reading: two-hypothesis checks + K-way check."""
    from ..investigation import _state_key
    from .verification import kway_posterior
    kw = kway_posterior(sc, diag, meas["node"], meas["reading_m"],
                        _state_key({"event_class": meas["resolved_event_class"], "location": meas["resolved_location"]}))
    return from_investigation(diag, rec, meas, kw["posterior"])


def operator_confirmed(event_class: str, location: Optional[str], operator_id: str, note: str) -> ConfirmedDiagnosis:
    """Record an operator's assertion. Validated only for consistency with the Net1 model, not for truth."""
    if not operator_id or not operator_id.strip():
        raise ValueError("operator confirmation requires a non-empty operator_id")
    if event_class not in C.CLASSES:
        raise ValueError(f"unknown event class '{event_class}'")
    locs = valid_locations(event_class)
    if (locs and location not in locs) or (not locs and location is not None):
        raise ValueError(f"location '{location}' is not valid for {event_class} in the Net1 model: {locs}")
    return ConfirmedDiagnosis("OPERATOR_CONFIRMED", event_class, location, None, f"operator:{operator_id.strip()}",
                              f"operator assertion (not verified by AquaScope): {note.strip() or '(no note)'}")
