# Gemini entry point — Zalo Automation

Read `AGENTS.md`, `PROJECT_SUMMARY.md`, `PROJECT_STATE.md` and the relevant
source files before changing the application. Preserve unfinished work, never
request or record secrets, and do not perform database, browser, external-service,
deployment, or production actions unless the Owner explicitly authorizes them.

Explain results in plain Vietnamese. The Owner provides product intent and tests
the visible behavior; the agent handles code inspection and technical checks.

Workflow entry: follow [AGENTS.md](AGENTS.md) (`DEC-036`, `DEC-040`, `DEC-047` Two-Track Workflow: `[FAST-TRACK]` vs `[FULL GATE]`, GitHub sync, and concise Aviation/Telegram reporting) as the single source of workflow rules; see [HANDOFF.md](HANDOFF.md) and [UPGRADE_PROGRESS.md](UPGRADE_PROGRESS.md) for context. Zalo/BCNB/LMS/Form safety boundaries remain authoritative.

Graphify rule (mandatory, 100% offline): when `graphify-out/graph.json` exists, always look up `graphify query` / `graphify explain` / `graphify path` before reading code files broadly, and run `$env:GRAPHIFY_BACKEND="none"; graphify update .` after modifying code. Keep `.graphifyignore` intact, always run 100% offline (`$env:GRAPHIFY_BACKEND="none"`, `--code-only`), and never call external APIs (see [.agents/rules/graphify.md](.agents/rules/graphify.md)).
