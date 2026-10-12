"""pdf-extract core: MinerU conversion + rule-based markdown cleaning.

Two seams are deliberately kept small and testable:

* ``clean(markdown) -> (cleaned_text, stats)`` is a pure function; it never
  touches MinerU, so the cleaning rules can be iterated on in milliseconds.
* ``convert(pdf, out_dir, *, runner=...) -> artifacts`` is the thin integration
  seam. The MinerU subprocess boundary is injectable, so the pipeline can be
  exercised without loading a model.

The persistent artifact is a named ``<name>_md/`` container holding
``cleaned.md`` plus ``image/``, with relative ``![](image/...)`` links. The
container is the PDF stem unless ``--name`` overrides it. The raw MinerU output
only ever lives
in a temp directory: deleted on success, kept (and reported) on failure.

Cleaning is fail-safe: rules only fire on a located anchor, and anything that is
not confidently publisher boilerplate is left untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:  # imported as a package (tests / installed layout)
    from .mineru_server import MineruApiServer, MineruServerError
    from .mineru_server import DEFAULT_PORT as DEFAULT_SERVER_PORT
except ImportError:  # executed directly as a script
    from mineru_server import MineruApiServer, MineruServerError
    from mineru_server import DEFAULT_PORT as DEFAULT_SERVER_PORT

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

CONFIG_FILENAME = "pdf-extract.env"
DEFAULT_TIER = "auto"
DEFAULT_OCR_MODE = "auto"
# ``convert`` is the low-level seam: it never receives "auto", routing happens
# above it, so it defaults to a concrete, safe tier.
DEFAULT_CONVERT_TIER = "flash"
DEFAULT_CONVERT_OCR_MODE = "txt"
DEFAULT_TIMEOUT = 600.0
TEMP_PREFIX = "mineru-pdf-extract-"

EXIT_OK = 0
EXIT_ARGUMENT = 1
EXIT_CONVERSION = 2
EXIT_WRITE = 3
EXIT_TIMEOUT = 4


def mineru_bin() -> str:
    return os.environ.get("PDF_EXTRACT_MINERU_BIN", DEFAULT_MINERU_BIN)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class PdfExtractError(Exception):
    """Base error carrying the process exit code for the CLI."""

    exit_code = 1

    def __init__(self, message: str, tmp_dir: Optional[str] = None):
        super().__init__(message)
        self.tmp_dir = tmp_dir


class ConversionError(PdfExtractError):
    exit_code = EXIT_CONVERSION


class WriteError(PdfExtractError):
    exit_code = EXIT_WRITE


class ConversionTimeout(PdfExtractError):
    exit_code = EXIT_TIMEOUT


# --------------------------------------------------------------------------- #
# MinerU / Python path resolution
# --------------------------------------------------------------------------- #
#
# The installed copy ships a pinned path in ``pdf-extract.env`` (see
# ``install.sh``), so it works with no environment set. ``PDF_EXTRACT_MINERU_BIN``
# is the escape hatch for a source checkout (no pinned config) or an advanced
# override; PATH covers a hand-installed MinerU. Nothing here is hard-coded to a
# machine-specific path.


def config_path() -> Path:
    """Location of the pinned install config (overridable for tests)."""
    override = os.environ.get("PDF_EXTRACT_CONFIG")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / CONFIG_FILENAME


def read_config() -> dict[str, str]:
    """Read the simple ``KEY=value`` pinned-config file; ``{}`` if absent."""
    path = config_path()
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def resolve_mineru_bin() -> str:
    """Resolve ``mineru-kit``: pinned config -> env -> PATH -> error."""
    pinned = read_config().get("MINERU_BIN")
    if pinned and Path(pinned).is_file():
        return pinned
    override = os.environ.get("PDF_EXTRACT_MINERU_BIN")
    if override:
        return override
    found = shutil.which("mineru-kit")
    if found:
        return found
    raise ConversionError(
        "mineru-kit not found: run ./install.sh, set PDF_EXTRACT_MINERU_BIN, "
        "or install MinerU following the README"
    )


def resolve_python() -> str:
    """Resolve the pipeline interpreter: env -> pinned config -> sibling -> python3."""
    override = os.environ.get("PDF_EXTRACT_PYTHON")
    if override:
        return override
    pinned = read_config().get("MINERU_PYTHON")
    if pinned:
        return pinned
    try:
        mineru = resolve_mineru_bin()
    except PdfExtractError:
        return "python3"
    sibling = Path(mineru).resolve().parent / "python"
    return str(sibling) if sibling.is_file() else "python3"


def mineru_bin() -> str:
    """Backwards-compatible name for :func:`resolve_mineru_bin`."""
    return resolve_mineru_bin()


# --------------------------------------------------------------------------- #
# Cleaning rules (pure)
# --------------------------------------------------------------------------- #

_CONTENTS_RE = re.compile(r"contents lists available at", re.IGNORECASE)
_JOURNAL_HOMEPAGE_RE = re.compile(r"journal homepage|^\s*homepage\s*:", re.IGNORECASE)
_REF_HEADING_RE = re.compile(r"^(references|bibliography)\s*:?\s*$", re.IGNORECASE)
_DOI_LINE_RE = re.compile(
    r"^\s*\[?\s*(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", re.IGNORECASE
)
_COPYRIGHT_RE = re.compile(
    r"©|\(c\)\s*\d{4}|\bcopyright\b|all rights reserved|\bISSN\b|\b\d{4}-\d{3}[\dxX]/",
    re.IGNORECASE,
)
_ARTICLE_HISTORY_RE = re.compile(
    r"^\s*received\b.*\b(accepted|available online)\b", re.IGNORECASE
)
# MinerU marks per-page footnotes (corresponding author, affiliations, funding)
# with this parser-level anchor. It is boilerplate, not article body.
_PAGE_FOOTNOTE_RE = re.compile(
    r"data-block-type=[\"']page_footnote[\"']"
    r"|class=[\"']docvortex-page-footnote[\"']",
    re.IGNORECASE,
)
_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:[-–—]\s*)?(?:page\s+)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?\s*(?:[-–—])?\s*$",
    re.IGNORECASE,
)
_MAX_BOILERPLATE_LINE = 200
_MASTHEAD_LOOKAHEAD = 5
# A level-1 ATX heading: ``# Title``. ``^#\s`` deliberately excludes ``##``.
_H1_RE = re.compile(r"^#\s+\S")
# MinerU emits images as ``![](images/xxx.jpg)``; the folder is renamed at write
# time, so the links are rewritten to match.
_IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\(images/([^)]+)\)")


def _is_references_heading(line: str) -> bool:
    """True only for a standalone heading line such as ``## References``."""
    text = line.strip().lstrip("#").strip().strip("*_").strip()
    return bool(_REF_HEADING_RE.match(text))


def _is_copyright_line(line: str) -> bool:
    if len(line) > _MAX_BOILERPLATE_LINE:
        return False
    return bool(_COPYRIGHT_RE.search(line))


def _is_page_number_line(line: str) -> bool:
    return bool(_PAGE_NUMBER_RE.match(line))


def _blank(lines: list[str], index: int) -> bool:
    return index < 0 or index >= len(lines) or not lines[index].strip()


@dataclass(frozen=True)
class _Structure:
    """The document skeleton cleaning rules read instead of re-deriving it."""

    title_line: Optional[int]
    references_line: Optional[int]
    protected: frozenset[int]


def _analyze_structure(lines: list[str]) -> _Structure:
    """Locate the document skeleton: title, references heading, protected lines.

    Protected lines are opaque to the structure parse and to every rule: the
    rebuild filters them out of ``remove`` in one place, so rules never track
    fences or ``<details>`` themselves. With no title or references anchor the
    corresponding field is ``None`` and that region is left untouched.
    """
    protected: set[int] = set()
    in_fence = False
    in_details = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            protected.add(index)
            in_fence = not in_fence
            continue
        if in_fence:
            protected.add(index)
            continue
        if "<details" in stripped:
            protected.add(index)
            in_details = True
            continue
        if "</details>" in stripped:
            protected.add(index)
            in_details = False
            continue
        if in_details:
            protected.add(index)

    title_line: Optional[int] = None
    references_line: Optional[int] = None
    for index, line in enumerate(lines):
        if index in protected:
            continue
        if references_line is None and _is_references_heading(line):
            references_line = index
            continue
        if title_line is None and _H1_RE.match(line):
            if references_line is None or index < references_line:
                title_line = index

    return _Structure(
        title_line=title_line,
        references_line=references_line,
        protected=frozenset(protected),
    )


def clean(markdown: str) -> tuple[str, dict]:
    """Strip publisher boilerplate from a MinerU markdown document.

    Returns the cleaned text and a stats dict with ``lines_*`` / ``chars_*``
    counters plus the ``references_cut`` / ``front_matter_cut`` flags. When no
    rule matches, the input is returned byte-for-byte unchanged (fail-safe).
    """
    lines = markdown.split("\n")
    total = len(lines)
    structure = _analyze_structure(lines)
    remove: set[int] = set()

    # Regions come straight from the structure: everything before the title is
    # front matter, everything from the references heading is back matter. With
    # no such anchor, that region is left untouched (fail-safe).
    front_matter_cut = False
    if structure.title_line is not None and structure.title_line > 0:
        remove.update(range(0, structure.title_line))
        front_matter_cut = True

    references_cut = False
    if structure.references_line is not None:
        remove.update(range(structure.references_line, total))
        references_cut = True

    # Masthead: the block from "Contents lists available at ..." through
    # "journal homepage: ...". This also swallows the journal name line that
    # sits between them.
    for index, line in enumerate(lines):
        if not _CONTENTS_RE.search(line):
            continue
        remove.add(index)
        end = None
        for look in range(index + 1, min(index + 1 + _MASTHEAD_LOOKAHEAD, total)):
            if _JOURNAL_HOMEPAGE_RE.search(lines[look]):
                end = look
                break
        if end is not None:
            remove.update(range(index + 1, end + 1))
        else:
            # No homepage anchor: only drop the immediately following short,
            # non-structural line (usually the journal name).
            for look in range(index + 1, min(index + 3, total)):
                candidate = lines[look]
                if not candidate.strip():
                    continue
                if candidate.lstrip().startswith(("#", "!", "<", "|")):
                    break
                remove.add(look)
                break

    # Page footnotes: MinerU's ``page_footnote`` marker; when it opens a
    # ``<small>`` element that does not close on the same line, swallow through
    # the closing tag.
    for index, line in enumerate(lines):
        if index in remove or not _PAGE_FOOTNOTE_RE.search(line):
            continue
        remove.add(index)
        if "</small>" in line:
            continue
        for look in range(index + 1, total):
            remove.add(look)
            if "</small>" in lines[look]:
                break

    # Line-level publisher noise and standalone page numbers. Protected lines
    # are filtered once at rebuild, so this rule does not track fences or
    # ``<details>`` itself.
    for index, line in enumerate(lines):
        if (
            _JOURNAL_HOMEPAGE_RE.search(line)
            or _DOI_LINE_RE.match(line)
            or _is_copyright_line(line)
            or _ARTICLE_HISTORY_RE.match(line)
        ):
            remove.add(index)
        elif (
            _is_page_number_line(line)
            and _blank(lines, index - 1)
            and _blank(lines, index + 1)
        ):
            remove.add(index)

    effective = remove - structure.protected
    if not effective:
        return markdown, _stats(
            markdown, markdown, references_cut=False, front_matter_cut=False
        )

    kept = [line for index, line in enumerate(lines) if index not in effective]
    while kept and not kept[-1].strip():
        kept.pop()
    while kept and not kept[0].strip():
        kept.pop(0)
    trailing = "\n" if kept else ""
    cleaned = "\n".join(kept) + trailing
    return cleaned, _stats(
        markdown,
        cleaned,
        references_cut=references_cut,
        front_matter_cut=front_matter_cut,
    )


def _stats(
    before: str,
    after: str,
    *,
    references_cut: bool,
    front_matter_cut: bool = False,
) -> dict:
    lines_before = len(before.split("\n"))
    lines_after = len(after.split("\n"))
    return {
        "lines_before": lines_before,
        "lines_after": lines_after,
        "lines_removed": lines_before - lines_after,
        "chars_before": len(before),
        "chars_after": len(after),
        "chars_removed": len(before) - len(after),
        "references_cut": references_cut,
        "front_matter_cut": front_matter_cut,
    }


# --------------------------------------------------------------------------- #
# MinerU adapter
# --------------------------------------------------------------------------- #


def run_mineru(
    pdf: Path,
    zip_path: Path,
    tier: str,
    ocr_mode: str,
    timeout: float,
    remote_url: Optional[str] = None,
) -> None:
    """Run ``mineru-kit parse -f zip`` (locally or against an api-server)."""
    command = [mineru_bin(), "parse", str(pdf), "-o", str(zip_path), "-f", "zip"]
    if remote_url:
        command += ["--remote-url", remote_url, "--tier", tier]
    else:
        command += ["--tier", tier, "--ocr-mode", ocr_mode]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionTimeout(f"mineru timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise ConversionError(f"mineru executable not found: {command[0]}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ConversionError(f"mineru exited with {completed.returncode}: {detail}")
    if not zip_path.exists():
        raise ConversionError("mineru did not produce an output archive")


Runner = Callable[[Path, Path, str, str, float], None]


def make_remote_runner(remote_url: str) -> Runner:
    """Bind a runner that parses each PDF through a shared api-server."""

    def runner(pdf: Path, zip_path: Path, tier: str, ocr_mode: str, timeout: float) -> None:
        run_mineru(pdf, zip_path, tier, ocr_mode, timeout, remote_url=remote_url)

    return runner


# --------------------------------------------------------------------------- #
# Conversion pipeline
# --------------------------------------------------------------------------- #


def _rewrite_image_links(markdown: str, folder: str) -> str:
    """Point MinerU's ``![](images/...)`` links at the renamed image folder."""
    return _IMAGE_LINK_RE.sub(
        lambda match: f"![{match.group(1)}]({folder}/{match.group(2)})", markdown
    )


def _referenced_images(markdown: str) -> set[str]:
    """Return the ``images/...`` paths the markdown actually links to."""
    return {match.group(2) for match in _IMAGE_LINK_RE.finditer(markdown)}


def _copy_referenced_images(
    images_src: Path, images_dst: Path, referenced: set[str]
) -> None:
    """Copy only the images the markdown references.

    MinerU writes a render of every detected object into ``images/`` but links
    only real figures from the markdown. Copying the whole folder therefore
    leaves orphan ``page_*_equation_*`` / ``page_*_table_*`` renders in the
    artifact, which the reader would mistake for figures. Referencing is the
    contract, so anything unlinked is not part of the document.
    """
    images_dst.mkdir(parents=True, exist_ok=True)
    if not images_src.is_dir():
        return
    for src in images_src.rglob("*"):
        if not src.is_file():
            continue
        name = src.relative_to(images_src).as_posix()
        if name not in referenced:
            continue
        dst = images_dst / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def convert(
    pdf: Path | str,
    out_dir: Path | str,
    *,
    tier: str = DEFAULT_CONVERT_TIER,
    ocr_mode: str = DEFAULT_CONVERT_OCR_MODE,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Optional[Runner] = None,
) -> dict:
    """Convert one PDF into ``out_dir``; return artifact paths + clean stats.

    The container is named by the caller; inside it the names are fixed:
    ``cleaned.md`` and ``image/``.
    """
    pdf = Path(pdf)
    out_dir = Path(out_dir)
    runner = runner or run_mineru

    tmp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    success = False
    try:
        zip_path = tmp_dir / "out.zip"
        runner(pdf, zip_path, tier, ocr_mode, timeout)

        if not zip_path.exists():
            raise ConversionError("conversion produced no archive")

        extract_dir = tmp_dir / "extracted"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        raw_markdown = extract_dir / "markdown.md"
        if not raw_markdown.exists():
            raise ConversionError("conversion archive is missing markdown.md")

        cleaned, stats = clean(raw_markdown.read_text(encoding="utf-8"))
        referenced = _referenced_images(cleaned)
        cleaned = _rewrite_image_links(cleaned, "image")

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            images_src = extract_dir / "images"
            images_dst = out_dir / "image"
            _copy_referenced_images(images_src, images_dst, referenced)
            markdown_dst = out_dir / "cleaned.md"
            markdown_dst.write_text(cleaned, encoding="utf-8")
        except OSError as exc:
            raise WriteError(f"failed to write artifacts: {exc}") from exc

        success = True
        return {
            "pdf": str(pdf),
            "markdown": str(markdown_dst),
            "images": str(images_dst),
            "tier": tier,
            "ocr_mode": ocr_mode,
            "stats": stats,
        }
    except PdfExtractError as exc:
        exc.tmp_dir = exc.tmp_dir or str(tmp_dir)
        raise
    except Exception as exc:  # unexpected -> treat as conversion failure
        raise ConversionError(f"unexpected failure: {exc}", tmp_dir=str(tmp_dir)) from exc
    finally:
        if success:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Tier routing (auto)
# --------------------------------------------------------------------------- #

ROUTING_SAMPLE_PAGES = 5
SPARSE_TEXT_CHARS = 100
FORMULA_SYMBOL_MIN = 20
FORMULA_DENSITY_MIN = 0.005
STRONG_STANDALONE_EQUATIONS = 2
WEAK_SYMBOL_MIN = 5
WEAK_EQUATION_REFS = 3

_MATH_SYMBOLS = frozenset(
    "αβγδεζηθικλμνξπρστυφχψω"
    "ΓΔΘΛΞΠΣΦΨΩ"
    "∑∏∫∮√∞≤≥≠≈≡±∓×÷⋅·∘∂∇∈∉⊂⊃⊆⊇∪∩∧∨→←↔⇒⇔∀∃∗†‡"
)
_EQUATION_ALONE_RE = re.compile(r"^\(\d{1,3}\)$")
_EQUATION_LINE_RE = re.compile(r"\(\d{1,3}\)\s*$")


@dataclass(frozen=True)
class TextSignals:
    """Heuristic features sampled from a PDF's text layer."""

    char_count: int
    math_symbols: int
    standalone_equations: int
    equation_refs: int

    @property
    def math_density(self) -> float:
        return self.math_symbols / self.char_count if self.char_count else 0.0


@dataclass(frozen=True)
class Routing:
    """Chosen MinerU tier/OCR mode plus the human-readable justification."""

    tier: str
    ocr_mode: str
    reason: str
    low_confidence: bool


def scan_text(text: str) -> TextSignals:
    """Count math symbols and equation-number signals in extracted text."""
    char_count = sum(1 for ch in text if not ch.isspace())
    math_symbols = sum(1 for ch in text if ch in _MATH_SYMBOLS)
    standalone_equations = 0
    equation_refs = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _EQUATION_ALONE_RE.match(stripped):
            standalone_equations += 1
        elif len(stripped) <= 120 and _EQUATION_LINE_RE.search(stripped):
            equation_refs += 1
    return TextSignals(char_count, math_symbols, standalone_equations, equation_refs)


def route_tier(signals: TextSignals) -> Routing:
    """Pick a tier from text-layer signals (scan -> OCR, formulas -> standard)."""
    if signals.char_count < SPARSE_TEXT_CHARS:
        if signals.char_count == 0:
            return Routing(
                "flash", "ocr", "no text layer (scanned PDF) -> flash+ocr", False
            )
        return Routing(
            "flash",
            "ocr",
            f"sparse text layer ({signals.char_count} non-space chars) -> flash+ocr",
            True,
        )

    strong_formula = (
        signals.math_symbols >= FORMULA_SYMBOL_MIN
        and signals.math_density >= FORMULA_DENSITY_MIN
    ) or signals.standalone_equations >= STRONG_STANDALONE_EQUATIONS
    if strong_formula:
        return Routing(
            "standard",
            "txt",
            f"detected formula signals ({signals.math_symbols} math symbols, "
            f"{signals.standalone_equations} equation numbers) -> standard+txt",
            False,
        )

    weak_formula = (
        signals.math_symbols >= WEAK_SYMBOL_MIN
        or signals.standalone_equations >= 1
        or signals.equation_refs >= WEAK_EQUATION_REFS
    )
    if weak_formula:
        return Routing(
            "flash",
            "txt",
            f"weak formula signals ({signals.math_symbols} math symbols, "
            f"{signals.standalone_equations} equation numbers, "
            f"{signals.equation_refs} equation refs) -> flash+txt "
            "(low confidence; rerun with --tier standard if formulas matter)",
            True,
        )

    return Routing(
        "flash", "txt", "text layer without formula signals -> flash+txt", False
    )


def extract_pdf_text(
    pdf: Path, max_pages: int = ROUTING_SAMPLE_PAGES
) -> Optional[str]:
    """Sample the text layer with pypdf; ``None`` means no extractor available."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(pdf))
        chunks = [page.extract_text() or "" for page in reader.pages[:max_pages]]
    except Exception:
        # Unreadable/absent text layer: fall back to OCR, MinerU will surface
        # any real corruption later.
        return ""
    return "\n".join(chunks)


def resolve_routing(
    pdf: Path, tier: str = DEFAULT_TIER, ocr_mode: str = DEFAULT_OCR_MODE
) -> Routing:
    """Resolve the effective tier/OCR mode, honouring a forced ``--tier``."""
    if tier != "auto":
        return Routing(tier, ocr_mode, f"tier forced to {tier} (ocr-mode {ocr_mode})", False)
    text = extract_pdf_text(pdf)
    if text is None:
        return Routing(
            "flash", "auto", "text extractor unavailable -> flash+auto", True
        )
    return route_tier(scan_text(text))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-extract",
        description="Convert text-layer PDFs into cleaned Markdown via MinerU.",
    )
    parser.add_argument("pdf", nargs="+", help="one or more PDFs to convert")
    parser.add_argument(
        "--out",
        default=None,
        help="unified output root; default is <name>_md/ next to each PDF",
    )
    parser.add_argument(
        "--name",
        action="append",
        default=None,
        metavar="BASENAME",
        help=(
            "container basename (repeat once per PDF, in input order); "
            "default is each PDF's filename"
        ),
    )
    parser.add_argument(
        "--tier", default=DEFAULT_TIER, help="MinerU parse tier (auto|flash|standard)"
    )
    parser.add_argument(
        "--ocr-mode", default=DEFAULT_OCR_MODE, help="MinerU OCR mode (auto|txt|ocr)"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable JSON result"
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=DEFAULT_SERVER_PORT,
        help="port for the shared MinerU api-server in batch standard mode",
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing output directory"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="mineru timeout in seconds"
    )
    return parser


def _is_plain_basename(name: str) -> bool:
    """A bare file name: no directory part, no ``..`` escape."""
    if not name or name in {".", ".."}:
        return False
    return "/" not in name and "\\" not in name


def _plan_targets(
    pdfs: list[Path], out_root: Optional[Path], names: Optional[list[str]]
) -> list[Path]:
    """Resolve each PDF's container dir (``<name>_md``).

    The container is named by ``--name`` when given, else the PDF stem. When two
    inputs resolve to the same container path they are suffixed ``_1``, ``_2``
    in input order, so a batch never overwrites itself. The names inside the
    container are fixed (``cleaned.md`` + ``image/``), so only the container has
    to be unique.
    """
    parents = [out_root if out_root is not None else pdf.parent for pdf in pdfs]
    resolved_names = names if names is not None else [None] * len(pdfs)
    stems = [
        name if name is not None else pdf.stem
        for name, pdf in zip(resolved_names, pdfs)
    ]
    raw = [parent / f"{stem}_md" for parent, stem in zip(parents, stems)]
    totals: dict[Path, int] = {}
    for target in raw:
        totals[target] = totals.get(target, 0) + 1
    seen: dict[Path, int] = {}
    targets: list[Path] = []
    for parent, stem, target in zip(parents, stems, raw):
        if totals[target] > 1:
            seen[target] = seen.get(target, 0) + 1
            stem = f"{stem}_{seen[target]}"
        targets.append(parent / f"{stem}_md")
    return targets


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _fail_argument(
    messages: list[str], use_json: bool, exit_code: int = EXIT_ARGUMENT
) -> int:
    if use_json:
        payload = {
            "results": [],
            "errors": [
                {"message": message, "exit_code": exit_code} for message in messages
            ],
            "any_low_confidence": False,
            "exit_code": exit_code,
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for message in messages:
            print(f"[pdf-extract] ERROR: {message}", file=sys.stderr)
    return exit_code


def _json_result(result: dict) -> dict:
    stats = result["stats"]
    return {
        "pdf": result["pdf"],
        "markdown": result["markdown"],
        "images": result["images"],
        "tier": result["tier"],
        "ocr_mode": result["ocr_mode"],
        "routing_reason": result["routing_reason"],
        "low_confidence": result["low_confidence"],
        "stats": stats,
    }


def _json_error(pdf: Path, routing: Routing, exc: PdfExtractError) -> dict:
    return {
        "pdf": str(pdf),
        "tier": routing.tier,
        "ocr_mode": routing.ocr_mode,
        "routing_reason": routing.reason,
        "low_confidence": routing.low_confidence,
        "message": str(exc),
        "exit_code": exc.exit_code,
        "tmp_dir": exc.tmp_dir,
    }


def _json_payload(results: list[dict], errors: list[dict], exit_code: int) -> dict:
    return {
        "results": [_json_result(result) for result in results],
        "errors": errors,
        "any_low_confidence": any(
            item["low_confidence"] for item in [*results, *errors]
        ),
        "exit_code": exit_code,
    }


def _install_termination_cleanup(server: MineruApiServer) -> Callable[[], None]:
    """Stop the shared server even if the process is SIGTERM/SIGINT-ed."""
    saved: dict[int, object] = {}

    def handler(signum: int, _frame: object) -> None:
        server.stop()
        signal.signal(signum, saved.get(signum, signal.SIG_DFL))
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            saved[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass

    def restore() -> None:
        for sig, previous in saved.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError):
                pass

    return restore


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    use_json = args.json
    pdfs = [Path(raw) for raw in args.pdf]

    missing = [pdf for pdf in pdfs if not pdf.is_file()]
    if missing:
        return _fail_argument([f"PDF not found: {pdf}" for pdf in missing], use_json)

    out_root = Path(args.out) if args.out else None

    duplicate_inputs: list[str] = []
    seen_inputs: set[Path] = set()
    for pdf in pdfs:
        key = pdf.resolve()
        if key in seen_inputs:
            duplicate_inputs.append(str(pdf))
        seen_inputs.add(key)
    if duplicate_inputs:
        return _fail_argument(
            [
                f"duplicate input PDF: {path} (the same file was passed more than once)"
                for path in duplicate_inputs
            ],
            use_json,
        )

    names = args.name
    if names is not None:
        names = [n[:-3] if n.lower().endswith(".md") else n for n in names]
        if len(names) != len(pdfs):
            return _fail_argument(
                [
                    f"got {len(names)} --name value(s) but {len(pdfs)} PDF(s); "
                    "provide one --name per PDF (e.g. --name 2 --name 3), "
                    "or omit --name to use each PDF's filename"
                ],
                use_json,
            )
        invalid = [n for n in names if not _is_plain_basename(n)]
        if invalid:
            return _fail_argument(
                [
                    f"invalid --name {n!r}: use a plain basename "
                    "(no path separators, no '..')"
                    for n in invalid
                ],
                use_json,
            )

    targets = _plan_targets(pdfs, out_root, names)

    existing = [target for target in targets if target.exists()]
    if existing and not args.force:
        return _fail_argument(
            [
                f"output directory already exists: {target} (use --force to overwrite)"
                for target in existing
            ],
            use_json,
        )
    for target in existing:
        _remove_path(target)

    if not use_json and len(pdfs) > 1 and out_root is None:
        print("[pdf-extract] no --out given; writing <name>_md/ next to each PDF")

    routings = [resolve_routing(pdf, args.tier, args.ocr_mode) for pdf in pdfs]

    server: Optional[MineruApiServer] = None
    runner: Optional[Runner] = None
    restore_signals: Callable[[], None] = lambda: None
    if len(pdfs) > 1 and any(routing.tier == "standard" for routing in routings):
        server = MineruApiServer(mineru_bin(), port=args.server_port)
        try:
            server.start()
        except MineruServerError as exc:
            return _fail_argument(
                [f"api-server failed: {exc}"], use_json, EXIT_CONVERSION
            )
        restore_signals = _install_termination_cleanup(server)
        runner = make_remote_runner(server.remote_url)
        if not use_json:
            print(
                f"[pdf-extract] api-server {'reused' if server.reused else 'started'} "
                f"at {server.remote_url}"
            )

    results: list[dict] = []
    errors: list[dict] = []
    try:
        for pdf, target, routing in zip(pdfs, targets, routings):
            if not use_json:
                print(
                    f"[pdf-extract] {pdf.name}: tier {routing.tier} "
                    f"(ocr-mode {routing.ocr_mode}) - {routing.reason}"
                )
            try:
                result = convert(
                    pdf,
                    target,
                    tier=routing.tier,
                    ocr_mode=routing.ocr_mode,
                    timeout=args.timeout,
                    runner=runner,
                )
            except PdfExtractError as exc:
                errors.append(_json_error(pdf, routing, exc))
                if not use_json:
                    _report_error(exc)
                continue
            result["routing_reason"] = routing.reason
            result["low_confidence"] = routing.low_confidence
            results.append(result)
            if not use_json:
                _report_success(result)
    finally:
        restore_signals()
        if server is not None:
            server.stop()

    exit_code = errors[0]["exit_code"] if errors else EXIT_OK
    if use_json:
        print(json.dumps(_json_payload(results, errors, exit_code), ensure_ascii=False, indent=2))
    return exit_code


def _report_success(result: dict) -> None:
    stats = result["stats"]
    print(
        "[pdf-extract] cleaned: removed "
        f"{stats['lines_removed']} lines / {stats['chars_removed']} chars "
        f"(kept {stats['lines_after']} lines / {stats['chars_after']} chars)"
    )
    print(f"[pdf-extract] output: {result['markdown']}")
    print(f"[pdf-extract] images: {result['images']}")


def _report_error(exc: PdfExtractError) -> None:
    print(f"[pdf-extract] ERROR: {exc}", file=sys.stderr)
    if exc.tmp_dir:
        print(f"[pdf-extract] temp dir kept: {exc.tmp_dir}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
