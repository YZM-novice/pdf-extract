---
name: pdf-extract
description: "Convert one or more local PDFs into cleaned Markdown using the local MinerU pipeline (no file contents are read into context). Use this whenever the user asks to turn a PDF into Markdown, extract a paper's text, clean MinerU output, or mentions a .pdf path they want converted. Also usable manually via /skill:pdf-extract (pi) or $pdf-extract (Codex)."
---

# pdf-extract

Turn one or more local PDFs into cleaned Markdown by running the deterministic
`pdf-extract` pipeline. The script does all the mechanical work: you only pass
path(s) in and report path(s)/stats out.

## Rules (non-negotiable)

1. **Check the cache first.** Before converting, check whether
   `<pdf>_md/cleaned.md` already exists (next to the PDF, or under `--out`).
   If it does, reuse it: do not re-run, and do not read the PDF. Cache hits are
   permanent and survive across sessions.
2. **Never read the PDF**, the raw MinerU markdown, or anything inside the temp
   working directory. Those are inputs/intermediates, not things to inspect.
3. Never read the PDF to "check" it first. The script routes and converts on its
   own; reading document bytes is forbidden and wastes tokens.
4. Do not invent or rewrite paths. Use the PDF path(s) exactly as the user gave
   them.
5. At the end, report only the artifact path(s) and the trim stats (plus a
   low-confidence note if any). Never paste Markdown content back to the user.
6. **Honour an explicit speed/quality choice.** When the user asks for a fast
   or quick validation (e.g. "快速验证", "quick check", or while iterating on
   cleaning rules), pass `tier="flash"` and `ocr_mode="txt"` so the script
   skips its auto-routing step. Use auto only when the user has expressed no
   preference.

## Steps

1. Collect the PDF path(s) from the user's message (one or more).
2. If it is a **batch** (more than one PDF) and the user did **not** specify a
   storage location, stop and ask/confirm where to write before running. The
   script only prints a reminder when `--out` is omitted; the confirmation is
   your job.
3. For each PDF, if `<pdf>_md/cleaned.md` already exists, skip it and report the
   cached artifact. Only convert the misses.
4. Run the conversion:
   - **pi**: if the `pdf-extract` tool is available, call it with `pdf_path`
     (and optional `tier` / `ocr_mode` / `name`; pass `tier="flash"` and
     `ocr_mode="txt"` for a fast validation run, and `name` when the user asks
     for a specific Markdown name). It returns paths + stats and never returns
     file content.
   - **Codex / shell**: run the installed terminal entry with the same script:
     ```bash
     pdf-extract <pdf...> [--out DIR]
     ```
     The entry runs the bundled `scripts/pdf_extract.py` with the frozen MinerU
     interpreter, so no environment activation is needed. If `pdf-extract` is
     not on `PATH`, run the bundled script directly:
     ```bash
     "${PDF_EXTRACT_PYTHON:-python3}" \
         ~/.codex/skills/pdf-extract/scripts/pdf_extract.py <pdf...> [--out DIR]
     ```
     Append `--tier flash --ocr-mode txt` for a fast validation run.
5. If the result reports `low_confidence` (or the script asks for a decision),
   stop and ask the user to choose `standard` (formula/VLM, slower but better on
   equations) or `flash` (fast). Re-run with `--tier standard` / `--tier flash`
   according to their choice.
6. If the run fails, report the failing stage, the original error, and the temp
   directory path the script kept. Do not open that temp directory to inspect
   the raw markdown or images.

## Harness differences

- **pi**: the extension provides the `pdf-extract` tool and fences `read` /
  `grep` / `bash` while a conversion runs.
- **Codex**: there is no equivalent extension. A shell hook blocks commands
  that touch `.pdf` or the MinerU temp tree, but it **cannot** stop Codex's own
  file-reading tool. The rules above (cache-first, never read the PDF) are the
  primary defense; the cleaned artifact is the durable fallback.

## Output layout

For each PDF the script writes, next to it (or under `--out`):

```
<container>_md/
├── cleaned.md     ← cleaned Markdown (the only persistent artifact)
└── image/         ← external images, linked as ![](image/xxx.jpg)
```

Only the container is named. `<container>` is the PDF filename, or the `name`
passed when the user asks for a specific output name (e.g. `--name 2` →
`2_md/`). The files inside it are fixed (`cleaned.md` + `image/`), so the
container isolates them and two PDFs can share an output root without colliding.
For a batch, pass one `name` per PDF in input order; a count mismatch is an
error.

The cleaned `cleaned.md` and its `image/` are the only things worth surfacing;
the temp directory holds the raw zip/markdown and is deleted on success.
