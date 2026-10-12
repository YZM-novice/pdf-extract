"""End-to-end seam against the real MinerU binary.

Gated behind PDF_EXTRACT_E2E=1 because it loads the local models and takes a
few seconds. Run with the interpreter that has MinerU and pypdf:

    PDF_EXTRACT_E2E=1 <mineru-env-python> -m unittest discover -s tests -v
"""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import pdf_extract  # noqa: E402


def _build_text_pdf(path: pathlib.Path) -> bool:
    """Build a small text-layer PDF with reportlab. Returns False if unavailable."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        return False

    width, height = letter
    doc = canvas.Canvas(str(path), pagesize=letter)

    def line(y, text, size=11, bold=False):
        doc.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        doc.drawString(54, y, text)

    line(height - 54, "Contents lists available at ScienceDirect", 9)
    line(height - 68, "Journal of Testing", 9)
    line(height - 82, "journal homepage: www.example.com/journal", 9)
    line(height - 120, "A Study of Clean Markdown Extraction", 18, bold=True)
    for i in range(8):
        line(height - 170 - i * 16, "This paper studies extraction of clean markdown text.")
    line(40, "1")
    doc.showPage()
    for i in range(8):
        line(height - 120 - i * 16, "We conclude the method works well.")
    line(40, "2")
    doc.save()
    return True


def _mineru_available() -> bool:
    try:
        return pathlib.Path(pdf_extract.resolve_mineru_bin()).exists()
    except pdf_extract.PdfExtractError:
        return False


@unittest.skipUnless(os.environ.get("PDF_EXTRACT_E2E") == "1", "set PDF_EXTRACT_E2E=1 to run")
@unittest.skipUnless(_mineru_available(), "MinerU binary not found")
class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "sample.pdf"
        if not _build_text_pdf(self.pdf):
            self.skipTest("reportlab not installed")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_text_pdf_converts_to_cleaned_markdown(self):
        out = self.tmp / "sample_md"

        result = pdf_extract.convert(self.pdf, out, tier="flash", ocr_mode="txt")

        md = pathlib.Path(result["markdown"])
        self.assertEqual(md, out / "cleaned.md")
        self.assertTrue(md.exists())
        text = md.read_text(encoding="utf-8")
        self.assertNotIn("data:image", text)
        self.assertIn("This paper studies extraction of clean markdown text", text)
        self.assertTrue(pathlib.Path(result["images"]).is_dir())


if __name__ == "__main__":
    unittest.main()
