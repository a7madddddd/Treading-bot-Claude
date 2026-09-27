"""Portfolio-level hard risk limits (D-0047).

Deterministic guardrails owned by code, not by an LLM. Per CLAUDE.md
§5: "Deterministic code owns: risk limits, position sizing,
stop/floor calculations..."; per CLAUDE.md §6: risk limits never
bypassed. This subsystem is the last check before a broker
submission occurs.
"""
