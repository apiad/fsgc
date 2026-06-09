# Development

For contributors: layout, the build-and-test gate, and how the repo coexists with the bundled Gemini CLI framework.

---

## Layout

```text
src/fsgc/
├── __main__.py       Typer app — `scan` (default), `inspect`, `cleanup-trails`
├── scanner.py        MCTS playout, DirectoryNode, parallel walk via asyncio.to_thread
├── engine.py         HeuristicEngine — match, sentinel verify, age × recovery score
├── aggregator.py     group_by_signature + group_behavioral_matches → proposal rows
├── trail.py          TrailStore (beaver-backed SQLite), fingerprint, TopChild ledger
├── sweeper.py        Sweeper — guards + send2trash + JSONL audit
├── behavior.py       BehavioralRuleManager — stale_dir / stale_file rules
├── config.py         SignatureManager — loads signatures.yaml
├── signatures.yaml   The shipped structural catalog
├── behaviors.yaml    The shipped behavioral catalog
└── ui/               Rich-based formatter + InquirerPy prompt

tests/                pytest + pytest-asyncio + coverage
docs/                 MkDocs/Material — this site
plans/                implementation plans (one per topic, dated)
research/             one-off investigations (RCAs, signature research)
know-how/             procedure docs (release, adding signatures, …)
```

---

## The gate

`make all` runs everything; CI runs the same commands.

| Target | What it runs |
| :--- | :--- |
| `make format` | `ruff check --fix .` + `ruff format .` |
| `make lint`   | `ruff check .` + `ruff format --check .` + `mypy src/` (strict) |
| `make test`   | `pytest` with coverage (`--cov=fsgc`) |
| `make all`    | `format → lint → test` |

Python 3.12+, `uv` for everything (`uv sync`, `uv run pytest`, etc.). Strict mypy (`tool.mypy.strict = true`) — annotate everything; no bare `Any` unless suppressed via `ANN401`.

Ruff selects `E, F, I, N, UP, B, ANN, S, PT, ARG` — annotations + bugbear + security. Tests get `S101` and `ANN` suppressed (asserts and helper signatures).

### Tests

Live in `tests/`, organized by module: `test_scanner.py`, `test_engine.py`, `test_sweeper.py`, `test_behavior.py`, `test_review_flow.py`, etc. Use real filesystem fixtures (`tmp_path`) over mocks where possible — the Sweeper bug fixed in v0.5.0 was hidden by a mock-friendly test that never created a real directory.

```python
@pytest.mark.asyncio
async def test_scanner_walk(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "file.txt").write_text("hello")

    scanner = Scanner(root=tmp_path)
    async for snapshot in scanner.scan():
        ...
```

---

## Adding signatures or behaviors

Both catalogs (`signatures.yaml`, `behaviors.yaml`) ship inside the package and are extended by adding entries. Procedure docs live in `know-how/`:

- `know-how/adding-signatures.md` — when to add a structural signature, how to pick the recovery tier, sentinel patterns.
- `know-how/adding-behaviors.md` — when a behavioral rule is the right tool vs. a signature; signal types and their kind-compatibility constraints.
- `know-how/releasing.md` — the cut-a-PyPI-release sequence (CHANGELOG, version bump, GitHub release, trusted-publishing config).

---

## Coexisting with the Gemini CLI framework

This repo is also a **Gemini CLI agent framework**: `GEMINI.md` is its system prompt, `.gemini/{agents,commands,hooks,settings.json,style-guide.md}` is its operating substrate. The journal-driven workflow under `journal/` and the plan files under `plans/` are conventions shared by both Claude and Gemini agents working here.

Claude / Codex / other agents: treat `.gemini/` and `GEMINI.md` as the human's editor configuration for a different agent — don't hand-edit them, don't rewrite them, and don't replicate the hook system for yourself. `pyproject.toml` excludes `.gemini` from both ruff and mypy for the same reason. Run everything through `make` — that's the shared contract.

The orientation for Claude/Codex specifically is in `AGENTS.md`, with topic-specific procedures under `know-how/*.md` — see `know-how/coexisting-with-gemini-framework.md` for the full posture.
