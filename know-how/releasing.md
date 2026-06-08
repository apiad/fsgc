# Releasing fsgc

How to cut a new version of `fsgc` to PyPI.

## When to reach for it

Whenever you've landed a user-visible change on `main` and want it on PyPI as a new version. Patch releases for fixes, minor for additive features, major for breaking CLI/signature schema changes (semver).

## The CI does the publish

`.github/workflows/ci.yml` has a `publish` job that triggers on a **GitHub Release published** event:

- It uses `astral-sh/setup-uv` + Python 3.13.
- It runs `uv build` then `uv publish`.
- Publishing is via PyPI **trusted publishing** (OIDC, `id-token: write`) — no PyPI token in the repo.

Your job is to produce a tagged Release; CI does the rest.

## Procedure

1. **Verify clean.** `make all` (format + lint + test). Don't release dirty.
2. **Update `CHANGELOG.md`.** Add a new `## [X.Y.Z] - YYYY-MM-DD` section under the most recent entry. Group bullets under `### Added / Changed / Fixed / Removed` per Keep a Changelog. One bullet per user-visible behavior — link to the plan or commit if the change is non-obvious.
3. **Bump the version in `pyproject.toml`.** Single source of truth: `[project] version = "X.Y.Z"`. There is no separate `__version__` to keep in sync.
4. **Bump the badge in `README.md`** if you want the README to reflect the new version (the badge text is hard-coded).
5. **Commit.** `chore(release): version X.Y.Z` is the convention used historically (see `git log --oneline`).
6. **Push.** `git push origin main`.
7. **Tag and release on GitHub.**
   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-from-tag
   ```
   Or open the release UI and paste the relevant CHANGELOG section. Tag format is `vX.Y.Z` (with the `v`).
8. **Watch the CI.** `gh run watch` — confirm both `test` and `publish` succeed. `publish` runs only on the release event, so it'll be the second run for that SHA.
9. **Verify on PyPI.** `pip index versions fsgc` (or visit https://pypi.org/project/fsgc/) — confirm the new version is listed.

## What can go wrong

- **`uv publish` 403 / trusted-publishing failure** — the PyPI trusted publisher config must list `apiad/fsgc` as the GitHub source. If the repo was just renamed, update the PyPI project's publisher settings (Manage → Publishing) from `apiad/gc` to `apiad/fsgc`. Old releases stay associated; new releases need the new name.
- **Version not bumped** — `uv publish` will refuse to upload a duplicate. Bump and re-tag.
- **CHANGELOG forgotten** — not blocking but unprofessional; amend before tagging if you catch it pre-push.

## Docs publish is separate

`.github/workflows/docs.yml` runs `mkdocs gh-deploy --force` on every push to `main` — docs go out automatically, not gated on a release.
