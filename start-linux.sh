#!/bin/bash
# Pocket Bridge – start (Linux).
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "One-time install of 'uv'…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || exit 1
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync --quiet --python 3.12 || exit 1
exec .venv/bin/python -m pocket_bridge tray
