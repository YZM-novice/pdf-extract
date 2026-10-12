#!/usr/bin/env bash
# Codex PreToolUse hook: keep the agent from reading a PDF or MinerU temp files.
#
# Codex currently fires PreToolUse/PostToolUse for shell commands only, and the
# hook JSON schema is not stable, so this reads the raw event on stdin and
# matches plain keywords instead of parsing structured fields.
#
# Exit 2 blocks the tool call. This is a best-effort shell-layer fence, not a
# hard guarantee: Codex's built-in file reader is out of hook reach (the skill's
# "reuse cleaned.md" rule and the delete-on-success temp dir are the backstop).
#
# The pdf-extract pipeline itself must always pass, otherwise conversion is
# impossible.
set -euo pipefail

payload="$(cat || true)"

shopt -s nocasematch
# The temp prefix itself contains "pdf-extract", so block it *before* the
# pipeline allow-list below.
if [[ "$payload" == *"mineru-pdf-extract-"* || "$payload" == *"TMPDIR"* ]]; then
	echo "[pdf-extract] blocked: reading the MinerU temp files is not allowed; use the pdf-extract skill/CLI instead." >&2
	exit 2
fi
if [[ "$payload" == *"pdf-extract"* || "$payload" == *"pdf_extract.py"* ]]; then
	exit 0
fi
if [[ "$payload" == *".pdf"* ]]; then
	echo "[pdf-extract] blocked: reading the source PDF is not allowed; use the pdf-extract skill/CLI instead." >&2
	exit 2
fi
exit 0
