"""Static configuration for AquaScope. Values here are design constants, not tuned on test data."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
NET1_INP = DATA_DIR / "networks" / "Net1.inp"
SCENARIO_DIR = DATA_DIR / "scenarios"
ARTIFACT_DIR = DATA_DIR / "artifacts"
for _d in (SCENARIO_DIR, ARTIFACT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

NETWORK_ID = "Net1"
FLOW_UNITS_CMH = 8  # EPANET EN_CMH (EPyT-Flow leaks require CMH or CFS) -> pressure m; flows converted to L/s

# Time base
DAY = 86400
SIM_DURATION = 3 * DAY
HYD_STEP = 900
REPORT_STEP = 1800
STEPS_PER_DAY = DAY // REPORT_STEP           # 48
REFERENCE_END = DAY                          # day 1 is the assumed event-free reference period
EVENT_START_MIN = 30 * 3600
EVENT_START_MAX = 44 * 3600

# Sensor placement (fixed). Remaining junctions are candidate locations for extra measurements.
PRESSURE_SENSORS = ["11", "13", "22", "31"]
FLOW_SENSORS = ["9", "110"]                  # pump discharge, tank pipe
JUNCTIONS = ["10", "11", "12", "13", "21", "22", "23", "31", "32"]
PIPES = ["10", "11", "12", "21", "22", "31", "110", "111", "112", "113", "121", "122"]
LEAK_PIPES = [p for p in PIPES if p != "110"]  # 110 is the 200 ft tank riser
# Valve disturbances only where flow is large enough for throttling to matter (v^2/2g > 0.002 m)
VALVE_PIPES = ["10", "11", "12", "111", "112", "121", "122"]
PIPE_DIAMETER_M = {"10": 18, "11": 14, "12": 10, "21": 10, "22": 12, "31": 6, "110": 18, "111": 10,
                   "112": 12, "113": 8, "121": 8, "122": 6}
PIPE_DIAMETER_M = {k: v * 0.0254 for k, v in PIPE_DIAMETER_M.items()}
DEMAND_NODES = [n for n in JUNCTIONS if n != "10"]  # node 10 has zero base demand
PUMP_ID = "9"

CLASSES = [
    "normal", "abrupt_leak", "incipient_leak", "sensor_bias", "sensor_drift",
    "sensor_noise", "sensor_stuck", "pump_disturbance", "valve_disturbance", "demand_anomaly",
]
CLASS_FAMILY = {
    "normal": "normal", "abrupt_leak": "leak", "incipient_leak": "leak",
    "sensor_bias": "sensor", "sensor_drift": "sensor", "sensor_noise": "sensor", "sensor_stuck": "sensor",
    "pump_disturbance": "operational", "valve_disturbance": "operational", "demand_anomaly": "demand",
}
SENSOR_CLASSES = {"sensor_bias", "sensor_drift", "sensor_noise", "sensor_stuck"}
LEAK_CLASSES = {"abrupt_leak", "incipient_leak"}
MISMATCH_LEVELS = [0.0, 0.05, 0.15]

# Default measurement noise (std). Configurable per scenario.
DEFAULT_PRESSURE_NOISE_M = 0.1
DEFAULT_FLOW_NOISE_REL = 0.01

SIMULATION_MODE = "EPyT-Flow / EPANET hydraulic simulation"
