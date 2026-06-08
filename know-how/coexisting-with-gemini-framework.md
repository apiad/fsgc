# Coexisting with the Gemini CLI framework

The repo is wired for a Gemini CLI agent. As Claude/Codex/etc., respect the boundary.

## When to reach for it

The first time you notice `GEMINI.md`, `.gemini/`, or the `journal/` directory and wonder whether to touch them. Also when a tool (ruff, mypy, formatter) wants to walk into `.gemini/` and lint it.

## What belongs to the Gemini framework

- **`GEMINI.md`** — Gemini's system prompt. Treat as another agent's configuration; don't edit.
- **`.gemini/agents/`** — sub-agent definitions used by the Gemini CLI workflow.
- **`.gemini/commands/`** — slash commands (`/plan`, `/research`, `/onboard`, `/draft`, `/revise`, `/cron`, `/issues`, …).
- **`.gemini/hooks/`** — pre/post-tool hooks that synchronize agent state with project state. Pure-Python utilities in `.gemini/hooks/utils.py`.
- **`.gemini/settings.json`** — model + tool config.
- **`.gemini/style-guide.md`** — writing style for `/draft` and `/revise`.
- **`journal/`** — append-only log driven by a journaling hook. Don't write here; don't reorder; don't delete.

`pyproject.toml` already excludes `.gemini` from both `[tool.ruff]` and `[tool.mypy]` — keep it that way.

## What's shared between agents

These conventions are common ground and you can/should use them:

- **`makefile`** — `make all`, `make test`, `make lint`, `make format`, `make check`. The shared contract for verification.
- **`TASKS.md`** — `[ ] / [/] / [x]` task list with archive section. Capture new work here.
- **`plans/<topic>.md`** — implementation plans, one per topic. Both frameworks read/write this.
- **`CHANGELOG.md`** — Keep-a-Changelog format. Both frameworks update on user-visible changes.
- **`research/<topic>.md`** — one-off investigations / RCAs.
- **`docs/`** — MkDocs/Material site; auto-deployed by `.github/workflows/docs.yml`.

## What's yours (Claude)

- **`AGENTS.md`** + **`know-how/*.md`** — your equivalent of `GEMINI.md` + `.gemini/commands/`. The know-how convention is documented in the Workspace at `vault/Atlas/Architecture/2026-05-04-repo-know-how-convention-design.md`.

## Practical rules

- **Don't replicate Gemini's hook system for yourself.** If you want auto-validation, run `make all` at the end of your task — that's what hooks would do anyway.
- **Don't run `ruff format` over `.gemini/`.** The exclude is in place; respect it.
- **Don't commit changes to `journal/`** unless you understand the Gemini hook contract well enough to know you won't break it. Safer default: leave it alone.
- **If Alex asks you to add a feature, don't also "migrate" the repo away from Gemini.** Two-framework coexistence is intentional; the rename to `fsgc` was already done — further rework needs an explicit ask.
