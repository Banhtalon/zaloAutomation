# Gemini entry point — Zalo Automation

Read `AGENTS.md`, `PROJECT_SUMMARY.md`, `PROJECT_STATE.md` and the relevant
source files before changing the application. Preserve unfinished work, never
request or record secrets, and do not perform database, browser, external-service,
deployment, or production actions unless the Owner explicitly authorizes them.

Explain results in plain Vietnamese. The Owner provides product intent and tests
the visible behavior; the agent handles code inspection and technical checks.

Workflow entry: read [HANDOFF.md](HANDOFF.md), [AGENTS.md](AGENTS.md) and [UPGRADE_PROGRESS.md](UPGRADE_PROGRESS.md). Current Owner instructions (`DEC-036`, `DEC-040` & 2026-11-13 `DEC-047` Two-Track Workflow: **Fast-track** for small UI/color/copy/formatting/doc tweaks runs targeted unit tests + `check_template_js.js` + scoped Ruff/Mypy without full isolated gate or independent review; **Full Gate** for browser automation, BCNB/Teaching writes, DB/schema, security/PII, or core multi-module business logic runs full `.\CHECK_QUALITY.ps1 -Isolated` + `gpt-6.1-sol` read-only review to `PASS`; both tracks update `CHANGELOG.md` + `PROJECT_STATE.md`, commit allowlisted files, push to GitHub, and report in ultra-concise "Aviation / Telegram" style) and Zalo safety boundaries remain authoritative.
