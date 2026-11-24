"""Remediation configuration. Engineering constraints are explicit, named and configurable."""
from __future__ import annotations

from .. import config as C

DECISION_TIME = C.SIM_DURATION            # interventions are applied at the end of the 72 h telemetry record
POST_HORIZON = 24 * 3600                   # simulate 24 h after the intervention
HORIZON = DECISION_TIME + POST_HORIZON     # 96 h
SETTLE = 3600                              # first hour after an intervention excluded from "after" metrics
BEFORE_WINDOW = 24 * 3600                  # "before" = the 24 h preceding the intervention

# Pressure-driven analysis (EPANET 2.2 PDA). Demand-driven analysis reports nonsense (e.g. -5e7 m) and
# 100 % delivery when supply is cut, so every remediation run is pressure-driven.
MIN_SERVICE_PRESSURE_M = 20.0
DEMAND_MODEL = {"type": 1, "pressure_min": 0.0, "pressure_required": MIN_SERVICE_PRESSURE_M,
                "pressure_exponent": 0.5}

CONSTRAINTS = {
    "all_junctions_connected_to_a_source": True,
    "min_service_pressure_m": MIN_SERVICE_PRESSURE_M,
    "min_delivered_fraction": 0.99,
    "tank_must_not_empty": True,
}

VERDICT_PASS = "PASSED CONFIGURED SIMULATION CONSTRAINTS — NOT VALIDATED FOR REAL-WORLD EXECUTION"
VERDICT_FAIL = "FAILED CONFIGURED SIMULATION CONSTRAINTS — SIMULATION RESULT ONLY"
SIMULATION_LABEL = "SIMULATED INTERVENTION ON EPANET Net1 — NOT AN OPERATIONAL INSTRUCTION"

# Net1 has no valve inventory. Physical isolation capability is UNKNOWN unless configured here as
# {"<pipe id>": "AVAILABLE" | "NOT_AVAILABLE"}. Simulations may still close any pipe ("idealised isolation").
VALVE_AVAILABILITY: dict[str, str] = {}
ISOLATION_ASSUMPTION = ("MODELLING ASSUMPTION: idealised isolation — the pipe is closed instantly and completely "
                        "in the hydraulic model. Physical valve availability is not known for Net1.")


# Diagnosis confirmation policy (configured, not tuned on data). Investigation confirms a fault only when the
# reading is consistent (<= 3 sigma from the winning prediction), was taken where the two hypotheses separate by
# >= investigation.MIN_SEPARATION, and the two-hypothesis posterior of the winner is >= this value.
CONFIRMATION_MIN_POSTERIOR = 0.95
CONFIRMATION_MAX_Z = 3.0          # winning hypothesis must predict the reading within this many sigma
# Selected on the VALIDATION split (select_policy.py, target <= 3 % wrong confirmations): the confirmed state must also
# hold >= 0.5 equal-prior posterior among the 6 leading physical explanations. Validation: 0 / 165 wrong (was 19 / 241).
CONFIRMATION_MIN_KWAY_POSTERIOR = 0.5
HYDRAULIC_UNLOCK_STATUSES = ("CONFIRMED_BY_INVESTIGATION", "OPERATOR_CONFIRMED")
NOT_EXECUTABLE = "NOT VALIDATED FOR REAL-WORLD EXECUTION"


# Ranking (M3). Outcomes are PREDICTED on the nominal model (mismatch 0), the only model an operator would have.
PREDICTION_MISMATCH = 0.0
# Minimum predicted benefit for an intervention to be recommended over doing nothing (configured, not tuned):
MIN_LEAK_VOLUME_AVOIDED_M3 = 1.0              # over the 23 h after-window
MIN_EXCESS_HEADLOSS_REMOVED_M = 0.5          # throttled pipe, median over the after-window
MIN_PUMP_HEAD_DEFICIT_REMOVED_M = 0.5         # pump head below healthy curve, median over pump-on samples
DEFAULT_START_TIME = 36 * 3600                # used only when no detection time is available


def valve_availability(pipe_id: str) -> str:
    return VALVE_AVAILABILITY.get(pipe_id, "UNKNOWN")
