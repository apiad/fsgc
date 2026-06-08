# AGENTS.md — fsgc

You're an AI agent picking up the **fsgc** codebase. This file is the door: orientation + an index into `know-how/`. Read it before touching the repo.

## What it is

A Python CLI (`fsgc`) that scans a filesystem with an MCTS-informed stochastic search, scores directories against a YAML-defined signature catalog (with sentinel verification — e.g. `node_modules` is only flagged if `package.json` is also present), and proposes interactive deletion of transient/stale data (build caches, virtualenvs, dep trees, OS junk). Three layers: **Scanner** (MCTS playout) → **HeuristicEngine** (mark phase) → **Aggregator** (sweep phase, groups garbage into selectable collections). Published to PyPI as `fsgc`; the GitHub repo is `apiad/fsgc`. CLI entry point in `src/fsgc/__main__.py`.

## Coexistence with the Gemini CLI framework — load-bearing

This repo is built around a **Gemini CLI agent framework**: `GEMINI.md` is its system prompt, `.gemini/{agents,commands,hooks,settings.json,style-guide.md}` is its operating substrate, and the journal-driven workflow (entries in `journal/`, plans in `plans/`) is its convention. Treat all of that as **the human's editor configuration for a different agent** — don't hand-edit `.gemini/`, don't rewrite `GEMINI.md`, don't replicate its hook system for yourself. `pyproject.toml` excludes `.gemini` from both ruff and mypy for the same reason.

When you (Claude / Codex / etc.) work here:

- Use `make` for everything that needs running — `make test`, `make lint`, `make format`, `make all`. The makefile is the shared contract both agents drive through.
- Add your work to `TASKS.md` and `plans/` (the conventions are common to both frameworks). Use the same `[ ] / [/] / [x]` task format and the same `plans/<topic>.md` shape — see existing entries.
- Keep `CHANGELOG.md` updated for any user-visible change. Keep-a-Changelog format, semver.
- Commit conventionally (`feat:`, `fix:`, `chore:`, `docs:`, …). One logical change per commit.

## Layout

```
src/fsgc/
├── __main__.py        Typer app — `fsgc scan`, default command, sweep, CLI options
├── scanner.py         MCTS-informed async scanner; DirectoryNode + parallel playouts via asyncio.to_thread
├── engine.py          HeuristicEngine — pattern matching, sentinel verification, scoring
├── aggregator.py      Group scored nodes by signature → selectable collections for the sweep
├── trail.py           GCTrail binary cache — prior scan history, informs MCTS selection
├── config.py          SignatureManager — loads signatures.yaml (default or ~/.config/fsgc/)
├── signatures.yaml    The knowledge base. New ecosystems get added here.
└── ui/                Rich-based formatter + InquirerPy prompt for interactive sweep

tests/                 pytest, asyncio, coverage gated via `--cov=fsgc`
docs/                  MkDocs/Material; auto-deployed to GitHub Pages on push to main
plans/                 implementation plans (one per topic, dated)
research/              one-off investigations (RCAs, signature research)
journal/               (Gemini framework's append-only log — leave alone)
know-how/              procedure docs (see index below)
```

## Conventions

- **Python 3.12+, `uv` for everything.** `uv sync`, `uv run pytest`, `uv run ruff check .`. Don't install with pip.
- **Strict mypy** (`tool.mypy.strict = true`) — annotate everything, no `Any` unless `ANN401`-suppressed.
- **Ruff lint profile** includes `ANN, B, S, PT, ARG, N, UP, I, E, F` — security + bugbear + annotations.
- **Tests** live in `tests/`, `pythonpath = ["src"]`. Coverage is computed automatically (`--cov=fsgc`).
- **Docs** mirror src changes — `docs/signatures.md` is the user-facing spec for the signature schema, update it when you add fields.

## Know-how

- **[releasing](know-how/releasing.md)** — *reach for it when* cutting a new version of `fsgc` to PyPI. Covers CHANGELOG, version bump, tag, GitHub Release, and what the CI auto-publishes.
- **[adding-signatures](know-how/adding-signatures.md)** — *reach for it when* adding a new garbage pattern (new language ecosystem, new build tool, new cache directory) or refining an existing signature with sentinels.
- **[coexisting-with-gemini-framework](know-how/coexisting-with-gemini-framework.md)** — *reach for it when* you notice `.gemini/` or `GEMINI.md` and wonder whether to touch them, or when a tool wants to lint/edit them.
