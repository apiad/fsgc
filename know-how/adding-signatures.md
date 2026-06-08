# Adding a new GC signature

How to extend fsgc's knowledge of what counts as filesystem garbage.

## When to reach for it

When you want fsgc to recognize a new ecosystem (a language, build tool, framework, OS junk pattern) or refine an existing signature to reduce false positives.

## The signature schema

Signatures live in `src/fsgc/signatures.yaml` and are loaded by `SignatureManager` in `src/fsgc/config.py`. Each entry:

```yaml
- name: "Human-readable name"        # surfaced in the UI; used as the group key by the aggregator
  pattern: "**/<dirname>"            # glob; `**/` prefix means "anywhere in the tree"
  priority: 0.0..1.0                 # higher = more confident it's garbage; drives ranking
  min_age_days: 0                    # optional; suppress unless mtime/atime is at least this old
  sentinels:                         # optional; files that must exist inside the dir to confirm
    - "filename-or-glob"
```

The dataclass is `Signature` in `config.py:7`. Schema additions need both:

1. A new field on the `Signature` dataclass + the parser in `SignatureManager.load()`.
2. An update to `docs/signatures.md` so the user-facing table reflects the new field.

## Sentinels — why they matter

Without sentinels, `**/build` would torch a user's source folder named `build`. Sentinels are the "trust-but-verify" half — the `HeuristicEngine` checks that the matched directory contains at least one sentinel before scoring it as garbage. Prefer **unique filenames** over common extensions:

- ✅ `pyvenv.cfg` for `.venv`
- ✅ `package.json` for `node_modules`
- ✅ `CACHEDIR.TAG` or `.rustc_info.json` for Rust `target/`
- ❌ `*.txt` (too common)

The sentinel match is **content-based, short-circuiting** — the scanner stops walking a directory once the first sentinel is found (see `scanner.py` and the `2026-03-18` perf fix in `plans/fix-scanner-performance.md`).

## Procedure

1. **Decide where the new pattern belongs.** Group by ecosystem in `signatures.yaml` — there are existing sections for Python, JS/Node, Rust, OS-junk, etc. Add the entry to the right block.
2. **Pick a conservative priority.** Start at `0.5` for unfamiliar patterns. Existing entries cluster around: `1.0` for pure-cache-with-zero-risk (`__pycache__`), `0.8–0.9` for build artifacts (`node_modules`, `.venv`), `0.5–0.7` for ambiguous (`bin`, `obj`).
3. **Add sentinels if there's any false-positive risk.** Skim the ecosystem's official docs for an unambiguous marker file.
4. **Add a test** in `tests/test_engine_sentinels.py` (sentinel verification) or `tests/test_config_sentinels.py` (config loading). Existing tests are the templates — copy one and adapt.
5. **Run `make test`.** Coverage is gated; missing tests for new code surface immediately.
6. **Update `docs/signatures.md`** if you changed the schema or added a notable ecosystem to the "Supported Ecosystems" section.
7. **Bump the CHANGELOG** under `### Added` (or `### Changed` for refinements).

## Researching new signatures

`research/garbage-collection-signatures/` and `research/build-systems-ml-cache-signatures.md` are the historical investigations — read those before scratch-researching a new ecosystem, the prior work covers most of the common ones. Update or extend those docs as you go.

## User overrides

Users can override the shipped signatures with `~/.config/fsgc/signatures.yaml` — `SignatureManager` prefers it when present (see `config.py:28-31`). This is a full replacement, not a merge — document this in the CHANGELOG if you change the schema in a way that breaks user files.
