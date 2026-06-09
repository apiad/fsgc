#!/usr/bin/env bash
# Stages a synthetic demo tree at $1 with various fake "garbage" so
# fsgc has something to find. Reproducible: same content, same dates.
set -e
ROOT="${1:?usage: stage-demo.sh <dir>}"
rm -rf "$ROOT"
mkdir -p "$ROOT"
cd "$ROOT"

# A node_modules with package.json sentinel (sentinel lives INSIDE the dir)
mkdir -p my-app/node_modules/{react,lodash,typescript}/{dist,src}
echo '{"name":"my-app","version":"1.0.0"}' > my-app/package.json
echo '{"name":"react","version":"18.0.0"}' > my-app/node_modules/package.json
truncate -s 320M my-app/node_modules/.bulk

# A __pycache__ tree (mark-eligible after 1 day)
mkdir -p data-pipeline/__pycache__
truncate -s 8M data-pipeline/__pycache__/loader.cpython-313.pyc
truncate -s 12M data-pipeline/__pycache__/transforms.cpython-313.pyc

# A .venv with TRIVIAL local recovery
mkdir -p analytics/.venv/{bin,lib}
echo "fake-venv" > analytics/.venv/pyvenv.cfg
truncate -s 480M analytics/.venv/lib/site-packages.bulk

# A .cache/uv (NETWORK recovery)
mkdir -p .cache/uv/wheels
truncate -s 1200M .cache/uv/wheels/torch-2.4.0-cp313.whl

# Stale code project: a .git/HEAD with an old mtime
mkdir -p old-prototype/.git
echo "ref: refs/heads/main" > old-prototype/.git/HEAD
truncate -s 18M old-prototype/big-asset.bin
# Backdate the HEAD by 300 days
touch -d "300 days ago" old-prototype/.git/HEAD

# An old download
mkdir -p Downloads
truncate -s 620M Downloads/ubuntu-installer.iso
touch -d "200 days ago" Downloads/ubuntu-installer.iso

# Backdate everything to 60 days so signature min_age cutoffs let them surface.
# Skip old-prototype/.git/HEAD and Downloads/ubuntu-installer.iso to preserve
# the very-old timestamps that drive the behavioral rules.
find . -depth \
    ! -path "./old-prototype/.git/HEAD" \
    ! -path "./Downloads/ubuntu-installer.iso" \
    -exec touch -d "60 days ago" {} +
