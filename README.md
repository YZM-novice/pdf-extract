# pdf-extract

Turn local PDFs into **cleaned Markdown** with a deterministic, local
[MinerU](https://github.com/opendatalab/MinerU) pipeline.

- Runs entirely on your machine. No uploads, no network service.
- The agent never reads the PDF or the raw MinerU output: it only passes paths
  in and reports artifact paths plus trim stats.
- One persistent artifact per PDF: `<name>_md/cleaned.md` + `<name>_md/image/`.
- Ships as an agent **skill** (pi and Codex) and a plain **CLI**, so you can use
  it with or without an agent.

[中文说明](README.zh-CN.md)

## Installation

### Prerequisites

- Linux (including WSL Ubuntu).
- Python `>=3.10,<3.15` (3.12 recommended).
- MinerU 4.x (`mineru>=4.0,<5`) and its models.
- Optional: `pypdf` (used only to sample the PDF text layer for auto-routing).
- Linux tools `ss` and `/proc` (used for the shared API server in batch mode).

### Option A — one-shot installer (recommended)

```bash
git clone https://github.com/YZM-novice/pdf-extract.git
cd pdf-extract
./install.sh
```

`install.sh` will:

1. find a suitable Python and reuse an existing MinerU (`mineru-kit` on
   `PATH`, a conda env, or `$PDF_EXTRACT_MINERU_PYTHON`); if none exists it
   creates a dedicated venv at `~/.local/share/pdf-extract/venv` and installs
   `mineru>=4.0,<5` (using `uv` when available, else `python -m venv` + `pip`);
2. ask before downloading MinerU models (~2 GB+);
3. install the skill for pi and/or Codex, the `pdf-extract` terminal entry, the
   pi extension, and (for Codex) a shell hook;
4. freeze the resolved absolute paths into `pdf-extract.env` next to the skill,
   so nothing needs to be activated or configured by hand.

Useful flags: `--pi`, `--codex`, `--all`, `--skills-only`, `--no-codex-hooks`,
`--no-models`, `--yes`.

### Option B — bring your own MinerU

Install MinerU 4.x yourself, then install only the skill:

```bash
# per the official MinerU Quick Start
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -U "mineru>=4.0,<5"
mineru-kit parse document.pdf -o document.md --tier flash --ocr-mode txt

# install the pdf-extract skill + CLI
cd /path/to/pdf-extract
./install.sh --skills-only
```

The first model-backed parse may download weights. `--tier flash --ocr-mode txt`
reads the PDF text layer without inference models; use `--ocr-mode ocr` for
scanned PDFs.

### MinerU basics

```bash
mineru-kit parse document.pdf -o document.md --tier standard
mineru-kit parse document.pdf -o document.md --tier flash --ocr-mode txt
mineru-kit models download      # fetch/verify models
```

### Uninstall

Delete the installed copies:

```bash
rm -rf ~/.agents/skills/pdf-extract ~/.codex/skills/pdf-extract
rm -f  ~/.local/bin/pdf-extract ~/.pi/agent/extensions/mineru-pdf-extract.ts
# Codex hook (edit or remove the entry in ~/.codex/hooks.json manually)
rm -rf ~/.codex/hooks/block-pdf-reads.sh
```

## Usage

### With an agent (implicit)

The skill is **implicitly invoked**: just ask for a PDF to become Markdown and
the agent uses it. Manual entry points are still available:

- pi: `/skill:pdf-extract`
- Codex: `$pdf-extract`

Before converting, the skill checks whether `<pdf>_md/cleaned.md` already
exists and reuses it. A successful conversion therefore keeps paying off across
sessions.

### CLI

```bash
pdf-extract paper.pdf                      # writes paper_md/ next to the PDF
pdf-extract a.pdf b.pdf --out ./out        # batch, shared output root
pdf-extract paper.pdf --tier flash --ocr-mode txt   # fast validation
pdf-extract paper.pdf --name 2             # container 2_md/
```

Options: `--out`, `--name` (one per PDF, in input order), `--tier`
(`auto|flash|standard`), `--ocr-mode` (`auto|txt|ocr`), `--json`, `--force`,
`--timeout`, `--server-port`.

### Output layout

```
paper_md/
├── cleaned.md     # cleaned Markdown (the only persistent artifact)
└── image/         # external images, linked as ![](image/xxx.jpg)
```

Only the container (`<name>_md/`) is named; the files inside are fixed.
`image/` contains exactly the images `cleaned.md` links to — MinerU's extra
equation/table renders are dropped. On failure the temp directory is kept and
reported; on success it is deleted.

### Switch back to explicit-only invocation

Implicit triggering is the default because it is friendlier for new users. To
require an explicit call instead:

- pi: add `disable-model-invocation: true` to the frontmatter of the installed
  `~/.agents/skills/pdf-extract/SKILL.md`.
- Codex: set `policy.allow_implicit_invocation: false` in
  `~/.codex/skills/pdf-extract/agents/openai.yaml`.

## pi vs Codex

| Capability | pi | Codex |
|---|---|---|
| Read interception | `read` / `grep` / `bash` via the extension | **shell commands only** (hook); the built-in file reader is not blocked |
| Manual entry | `/skill:pdf-extract` | `$pdf-extract` |
| Tool | `pdf-extract` tool | shell CLI (`pdf-extract`) |

**Honest limitation:** Codex's `PreToolUse` / `PostToolUse` hooks currently fire
for shell commands only, and load from the config layer
(`~/.codex/hooks.json`, requiring `[features].codex_hooks = true`), not from a
skill. Codex therefore **cannot be fully prevented from using its own file
reader**. The skill rules ("never read the PDF", "reuse `cleaned.md`") plus the
shell hook are the defense; once an artifact exists, the cached `cleaned.md`
means the source PDF never needs to be read again.

## License

MIT. See [LICENSE](LICENSE).
