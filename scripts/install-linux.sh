#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="${CITRUS_INSTALL_DIR:-$HOME/.local/share/citrus-agent/venv}"
python_bin="${CITRUS_PYTHON:-python3}"
"$python_bin" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
"$python_bin" -m venv "$install_dir"
"$install_dir/bin/python" -m pip install "$repo_dir[claude]"
mkdir -p "$HOME/.local/bin"
ln -sfn "$install_dir/bin/citrus-agent" "$HOME/.local/bin/citrus-agent"
printf '%s\n' 'Installed. Add ~/.local/bin to PATH, then run citrus-agent init --hub https://your-hub'
