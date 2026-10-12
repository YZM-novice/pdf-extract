#!/usr/bin/env bash
# One-shot installer for pdf-extract.
#
# It sets up as much as it can without asking the user to configure anything:
#   1. find Python 3.10-3.14 and an existing MinerU, or create a dedicated venv;
#   2. optionally download the MinerU models;
#   3. install the skill for pi and/or Codex;
#   4. freeze the resolved absolute paths into pdf-extract.env, the terminal
#      entry and (for pi) the extension, so no environment activation is needed;
#   5. install the terminal entry `pdf-extract` and, for Codex, a shell hook.
#
# The repo is the single source of truth; global locations are copy-based
# install products (no symlinks). Re-run after pulling changes.
#
# Usage:
#   ./install.sh [--pi|--codex|--all] [--skills-only] [--no-codex-hooks]
#                [--no-models] [--yes]
#
# Overridable (used by tests, and for non-default homes):
#   PDF_EXTRACT_SKILL_DIR         default: ~/.agents/skills/pdf-extract
#   PDF_EXTRACT_CODEX_SKILL_DIR   default: ~/.codex/skills/pdf-extract
#   PDF_EXTRACT_BIN_DIR           default: ~/.local/bin
#   PDF_EXTRACT_EXTENSION_DIR     default: ~/.pi/agent/extensions
#   PDF_EXTRACT_CODEX_DIR         default: ~/.codex
#   PDF_EXTRACT_VENV_DIR          default: ~/.local/share/pdf-extract/venv
#   PDF_EXTRACT_MINERU_PYTHON     reuse this interpreter's MinerU instead
#   PDF_EXTRACT_MINERU_BIN        reuse this mineru-kit instead
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

skill_dir="${PDF_EXTRACT_SKILL_DIR:-$HOME/.agents/skills/pdf-extract}"
codex_skill_dir="${PDF_EXTRACT_CODEX_SKILL_DIR:-$HOME/.codex/skills/pdf-extract}"
bin_dir="${PDF_EXTRACT_BIN_DIR:-$HOME/.local/bin}"
ext_dir="${PDF_EXTRACT_EXTENSION_DIR:-$HOME/.pi/agent/extensions}"
codex_dir="${PDF_EXTRACT_CODEX_DIR:-$HOME/.codex}"
venv_dir="${PDF_EXTRACT_VENV_DIR:-$HOME/.local/share/pdf-extract/venv}"

install_pi=false
install_codex=false
skills_only=false
codex_hooks=true
download_models=true
assume_yes=false

die() {
	echo "[install] ERROR: $*" >&2
	exit 1
}

while [ "$#" -gt 0 ]; do
	case "$1" in
	--pi) install_pi=true ;;
	--codex) install_codex=true ;;
	--all)
		install_pi=true
		install_codex=true
		;;
	--skills-only) skills_only=true ;;
	--no-codex-hooks) codex_hooks=false ;;
	--no-models) download_models=false ;;
	--yes) assume_yes=true ;;
	-h | --help)
		sed -n '2,30p' "${BASH_SOURCE[0]}"
		exit 0
		;;
	*) die "unknown option: $1" ;;
	esac
	shift
done

if [ "$install_pi" = false ] && [ "$install_codex" = false ]; then
	if [ -d "$HOME/.pi" ] || command -v pi >/dev/null 2>&1; then
		install_pi=true
	fi
	if [ -d "$HOME/.codex" ] || command -v codex >/dev/null 2>&1; then
		install_codex=true
	fi
	# Nothing detected: install the pi layout, the historical default.
	if [ "$install_pi" = false ] && [ "$install_codex" = false ]; then
		install_pi=true
	fi
fi

# --------------------------------------------------------------------------- #
# Python + MinerU discovery
# --------------------------------------------------------------------------- #

python_in_range() {
	"$1" -c 'import sys; v = sys.version_info[:2]; raise SystemExit(0 if (3, 10) <= v < (3, 15) else 1)' \
		>/dev/null 2>&1
}

find_python() {
	for candidate in python3.12 python3.13 python3.11 python3.10 python3; do
		if command -v "$candidate" >/dev/null 2>&1 && python_in_range "$candidate"; then
			command -v "$candidate"
			return 0
		fi
	done
	return 1
}

mineru_bin=""
mineru_python=""

set_mineru_from_python() {
	mineru_python="$1"
	local sibling
	sibling="$(dirname -- "$mineru_python")/mineru-kit"
	if [ -x "$sibling" ]; then
		mineru_bin="$sibling"
		return 0
	fi
	return 1
}

discover_mineru() {
	if [ -n "${PDF_EXTRACT_MINERU_BIN:-}" ]; then
		if [ -x "$PDF_EXTRACT_MINERU_BIN" ]; then
			mineru_bin="$PDF_EXTRACT_MINERU_BIN"
			if [ -n "${PDF_EXTRACT_MINERU_PYTHON:-}" ]; then
				mineru_python="$PDF_EXTRACT_MINERU_PYTHON"
			elif [ -x "$(dirname -- "$mineru_bin")/python" ]; then
				mineru_python="$(dirname -- "$mineru_bin")/python"
			fi
			return 0
		fi
	fi
	if [ -n "${PDF_EXTRACT_MINERU_PYTHON:-}" ] && set_mineru_from_python "$PDF_EXTRACT_MINERU_PYTHON"; then
		return 0
	fi
	if command -v mineru-kit >/dev/null 2>&1; then
		mineru_bin="$(command -v mineru-kit)"
		set_mineru_from_python "$(dirname -- "$mineru_bin")/python" || mineru_python="$(find_python || echo python3)"
		return 0
	fi
	# Common conda / virtualenv layouts.
	local candidate
	for candidate in \
		"$HOME"/miniconda3/envs/*/bin/mineru-kit \
		"$HOME"/anaconda3/envs/*/bin/mineru-kit \
		"$HOME"/.conda/envs/*/bin/mineru-kit \
		/opt/conda/envs/*/bin/mineru-kit; do
		if [ -x "$candidate" ]; then
			mineru_bin="$candidate"
			set_mineru_from_python "$(dirname -- "$mineru_bin")/python" || mineru_python="$(find_python || echo python3)"
			return 0
		fi
	done
	if [ -x "$venv_dir/bin/mineru-kit" ]; then
		mineru_bin="$venv_dir/bin/mineru-kit"
		mineru_python="$venv_dir/bin/python"
		return 0
	fi
	return 1
}

create_mineru_venv() {
	local py
	py="$(find_python)" || die "Python 3.10-3.14 not found; install Python 3.12 and re-run"
	echo "[install] installing MinerU into $venv_dir (this can take a while)"
	if command -v uv >/dev/null 2>&1; then
		uv venv --python "$py" "$venv_dir"
		uv pip install --python "$venv_dir/bin/python" -U "mineru>=4.0,<5"
	else
		"$py" -m venv "$venv_dir"
		"$venv_dir/bin/python" -m pip install -U "mineru>=4.0,<5"
	fi
	[ -x "$venv_dir/bin/mineru-kit" ] || die "MinerU install did not produce $venv_dir/bin/mineru-kit"
	mineru_bin="$venv_dir/bin/mineru-kit"
	mineru_python="$venv_dir/bin/python"
}

if [ "$skills_only" = true ]; then
	discover_mineru || true
else
	discover_mineru || create_mineru_venv
	if [ "$download_models" = true ]; then
		do_download=true
		if [ "$assume_yes" = false ] && [ -t 0 ]; then
			read -r -p "[install] download MinerU models now (~2GB+)? [Y/n] " answer || answer=""
			case "$answer" in
			[nN]*) do_download=false ;;
			esac
		fi
		if [ "$do_download" = true ]; then
			"$mineru_bin" models download
		fi
	fi
fi

mineru_python="${mineru_python:-$(find_python || echo python3)}"

# --------------------------------------------------------------------------- #
# Install products
# --------------------------------------------------------------------------- #

install_skill_copy() {
	local dest="$1"
	mkdir -p "$dest/scripts" "$dest/agents"
	cp -f "$repo_dir/SKILL.md" "$dest/SKILL.md"
	cp -f "$repo_dir/scripts/pdf_extract.py" "$dest/scripts/pdf_extract.py"
	cp -f "$repo_dir/scripts/mineru_server.py" "$dest/scripts/mineru_server.py"
	cp -f "$repo_dir/agents/openai.yaml" "$dest/agents/openai.yaml"
	echo "[install] skill -> $dest"
}

write_pinned_config() {
	local dest="$1"
	cat >"$dest/pdf-extract.env" <<EOF
# Written by install.sh. Do not edit; re-run the installer instead.
MINERU_BIN=$mineru_bin
MINERU_PYTHON=$mineru_python
EOF
}

if [ "$install_pi" = true ]; then
	install_skill_copy "$skill_dir"
	write_pinned_config "$skill_dir"
fi
if [ "$install_codex" = true ]; then
	install_skill_copy "$codex_skill_dir"
	write_pinned_config "$codex_skill_dir"
fi

# Terminal entry: freeze the interpreter + script path so activation is unneeded.
if [ "$install_pi" = true ]; then
	entry_skill_dir="$skill_dir"
else
	entry_skill_dir="$codex_skill_dir"
fi
if [ "$install_pi" = true ] || [ "$install_codex" = true ]; then
	mkdir -p "$bin_dir"
	cat >"$bin_dir/pdf-extract" <<EOF
#!/usr/bin/env bash
exec "$mineru_python" "$entry_skill_dir/scripts/pdf_extract.py" "\$@"
EOF
	chmod +x "$bin_dir/pdf-extract"
	echo "[install] bin   -> $bin_dir/pdf-extract"
	case ":$PATH:" in
	*":$bin_dir:"*) ;;
	*) echo "[install] note: $bin_dir is not on PATH; add it to use the pdf-extract command" ;;
	esac
fi

if [ "$install_pi" = true ]; then
	mkdir -p "$ext_dir"
	cp -f "$repo_dir/extensions/mineru-pdf-extract.ts" "$ext_dir/mineru-pdf-extract.ts"
	echo "[install] ext   -> $ext_dir/mineru-pdf-extract.ts"
fi

install_codex_hook() {
	echo "[install] hooks -> $codex_dir/hooks.json"
	mkdir -p "$codex_dir/hooks"
	cp -f "$repo_dir/hooks/codex/block-pdf-reads.sh" "$codex_dir/hooks/block-pdf-reads.sh"
	chmod +x "$codex_dir/hooks/block-pdf-reads.sh"
	"$mineru_python" - "$codex_dir/hooks.json" "$codex_dir/hooks/block-pdf-reads.sh" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
command = sys.argv[2]
data = {}
if path.is_file():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        data = {}
hooks = data.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
entry = next(
    (item for item in pre if isinstance(item, dict) and item.get("matcher") == "Bash"),
    None,
)
if entry is None:
    entry = {"matcher": "Bash", "hooks": []}
    pre.append(entry)
commands = entry.setdefault("hooks", [])
if not any(
    isinstance(item, dict) and item.get("command") == command for item in commands
):
    commands.append({"type": "command", "command": command})
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
	# Enable the feature flag without clobbering the rest of config.toml.
	"$mineru_python" - "$codex_dir/config.toml" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
out = []
in_features = False
has_features = False
flag_set = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        in_features = stripped == "[features]"
        has_features = has_features or in_features
        out.append(line)
        continue
    if in_features and re.match(r"^\s*codex_hooks\s*=", line):
        out.append("codex_hooks = true")
        flag_set = True
        continue
    out.append(line)
if not has_features:
    if out and out[-1].strip():
        out.append("")
    out.append("[features]")
    out.append("codex_hooks = true")
elif not flag_set:
    index = next(i for i, line in enumerate(out) if line.strip() == "[features]")
    out.insert(index + 1, "codex_hooks = true")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

if [ "$install_codex" = true ] && [ "$codex_hooks" = true ]; then
	install_codex_hook
fi

echo "[install] done"
