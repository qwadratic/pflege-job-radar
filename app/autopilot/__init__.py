"""Autopilot: the operator console for the recruiting funnel (docs/autopilot.md).

Proof of concept on synthetic data. Nothing here talks to WhatsApp, a mailbox or Meta; the engine is a
deterministic simulation with a virtual clock. Modules:

  db.py       SQLite schema + helpers (data/autopilot.sqlite, regenerable)
  seed.py     deterministic synthetic dataset, anchored on the real clinic registry
  engine.py   Luna's rules: next_action(), apply_inbound(), tick()
  matching.py candidate <-> clinic scoring (transparent weighted sum)
  api.py      FastAPI router mounted at /api/autopilot
"""
