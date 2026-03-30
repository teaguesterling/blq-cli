# Unified Service Layer — Phase 1: Extract Services

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `src/blq/services/` with shared business logic functions that both CLI and MCP can call, without changing any existing callers yet.

**Architecture:** Each service function takes a `BlqStorage` instance and parameters, returns structured dicts/lists. No argparse, no MCP, no output formatting. Phase 1 is additive only — existing CLI and MCP code stays untouched. Phase 2 (separate plan) will wire callers to use these services.

**Tech Stack:** Python 3.12+, DuckDB, existing `BlqStorage` from `src/blq/storage.py`

---

## Tasks

1. Service package + ref resolution
2. Query services (status, history, events, diff)
3. Inspect service (source, log, git, fingerprint context)
4. Execution service (RunResult to concise dict)
5. Package init + full test suite
6. Documentation

See full task details in the plan body above (conversation context).
