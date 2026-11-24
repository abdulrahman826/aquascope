"""AquaScope Remediation Intelligence Engine (independent module).

M1 scope: pressure-driven intervention simulation (simulate.py) and before/after hydraulic outcome
metrics with configured constraint checks (metrics.py). Nothing here modifies the existing
diagnosis / simulation / investigation engine; it only reads its public functions and constants.

All outputs are SIMULATION RESULTS on EPANET Net1. They are not operational instructions.
"""
