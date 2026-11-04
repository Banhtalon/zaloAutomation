# Gemini entry point — Zalo Automation

Read `AGENTS.md`, `PROJECT_SUMMARY.md`, `PROJECT_STATE.md` and the relevant
source files before changing the application. Preserve unfinished work, never
request or record secrets, and do not perform database, browser, external-service,
deployment, or production actions unless the Owner explicitly authorizes them.

Explain results in plain Vietnamese. The Owner provides product intent and tests
the visible behavior; the agent handles code inspection and technical checks.

Workflow entry: read [HANDOFF.md](HANDOFF.md), [AGENTS.md](AGENTS.md) and [UPGRADE_PROGRESS.md](UPGRADE_PROGRESS.md). Current Owner instructions (2026-10-22 `DEC-036` & 2026-11-04 `DEC-040`: Lead self-implements each bounded part -> runs full tests -> invokes `gpt-6.1-sol` read-only for independent review -> updates `CHANGELOG.md` clearly, commits, and pushes to GitHub as the authoritative source of truth -> reports in ultra-concise "Aviation / Telegram" style) and Zalo safety boundaries remain authoritative.
