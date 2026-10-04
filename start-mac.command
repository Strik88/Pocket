#!/bin/bash
# Pocket Bridge – dubbelklik om te starten (macOS).
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "Eenmalige installatie van 'uv' (regelt Python voor je)… / One-time install of 'uv'…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || { echo "Installeren van uv mislukt."; read -r -p "Druk op Enter…"; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Pocket Bridge voorbereiden… (de eerste keer duurt dit ongeveer een minuut)"
uv sync --quiet --python 3.12 || { echo "Installatie mislukt / install failed."; read -r -p "Druk op Enter…"; exit 1; }
exec .venv/bin/python -m pocket_bridge web
