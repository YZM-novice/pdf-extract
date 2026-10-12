"""Ticket 03: automatic tier routing, decision reasons, and low-confidence JSON."""

import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import pdf_extract  # noqa: E402
from tests.test_batch import make_pdf, make_runner  # noqa: E402


def plain_text_repetitions(n=200):
    return "reliability analysis and maintenance planning for engineered systems. " * n


class ScanTextTest(unittest.TestCase):
    def test_counts_math_symbols(self):
        signals = pdf_extract.scan_text("The model uses α, β and γ with ∫ f(x) dx.\n")

        self.assertEqual(signals.math_symbols, 4)
        self.assertGreater(signals.char_count, 0)

    def test_counts_standalone_equations_and_refs_separately(self):
        text = "Prose line.\n(1)\nAs shown in equation (2)\n"

        signals = pdf_extract.scan_text(text)

        self.assertEqual(signals.standalone_equations, 1)
        self.assertEqual(signals.equation_refs, 1)

    def test_ignores_blank_lines(self):
        self.assertEqual(pdf_extract.scan_text("\n\n   \n").char_count, 0)


class RouteTierTest(unittest.TestCase):
    def test_scanned_pdf_goes_flash_ocr(self):
        routing = pdf_extract.route_tier(pdf_extract.scan_text(""))

        self.assertEqual((routing.tier, routing.ocr_mode), ("flash", "ocr"))
        self.assertFalse(routing.low_confidence)
        self.assertIn("scanned", routing.reason.lower())

    def test_sparse_text_layer_is_low_confidence(self):
        routing = pdf_extract.route_tier(pdf_extract.scan_text("abc"))

        self.assertEqual((routing.tier, routing.ocr_mode), ("flash", "ocr"))
        self.assertTrue(routing.low_confidence)

    def test_plain_text_goes_flash_txt(self):
        routing = pdf_extract.route_tier(pdf_extract.scan_text(plain_text_repetitions()))

        self.assertEqual((routing.tier, routing.ocr_mode), ("flash", "txt"))
        self.assertFalse(routing.low_confidence)

    def test_formula_heavy_text_goes_standard(self):
        text = (
            "The variables α β γ δ τ σ μ λ and operators ∑ ∫ ≤ ≥ ∞ appear often. " * 20
        )

        routing = pdf_extract.route_tier(pdf_extract.scan_text(text))

        self.assertEqual((routing.tier, routing.ocr_mode), ("standard", "txt"))
        self.assertFalse(routing.low_confidence)
        self.assertIn("formula", routing.reason.lower())

    def test_standalone_equations_trigger_standard(self):
        text = plain_text_repetitions(80) + "\n(1)\n...\n(2)\n"

        routing = pdf_extract.route_tier(pdf_extract.scan_text(text))

        self.assertEqual(routing.tier, "standard")
        self.assertFalse(routing.low_confidence)

    def test_borderline_formula_signals_are_low_confidence(self):
        text = plain_text_repetitions(100) + " α β γ δ ε ζ η θ ι κ"

        routing = pdf_extract.route_tier(pdf_extract.scan_text(text))

        self.assertEqual((routing.tier, routing.ocr_mode), ("flash", "txt"))
        self.assertTrue(routing.low_confidence)
        self.assertIn("weak", routing.reason.lower())


class ResolveRoutingTest(unittest.TestCase):
    def test_forced_tier_skips_detection(self):
        with mock.patch.object(
            pdf_extract, "extract_pdf_text", side_effect=AssertionError("must not read")
        ):
            routing = pdf_extract.resolve_routing(
                pathlib.Path("x.pdf"), tier="standard", ocr_mode="txt"
            )

        self.assertEqual((routing.tier, routing.ocr_mode), ("standard", "txt"))
        self.assertFalse(routing.low_confidence)
        self.assertIn("forced", routing.reason.lower())

    def test_auto_reads_text_layer(self):
        with mock.patch.object(
            pdf_extract, "extract_pdf_text", return_value=plain_text_repetitions()
        ):
            routing = pdf_extract.resolve_routing(pathlib.Path("x.pdf"), tier="auto")

        self.assertEqual(routing.tier, "flash")
        self.assertFalse(routing.low_confidence)

    def test_missing_extractor_is_low_confidence(self):
        with mock.patch.object(pdf_extract, "extract_pdf_text", return_value=None):
            routing = pdf_extract.resolve_routing(pathlib.Path("x.pdf"), tier="auto")

        self.assertTrue(routing.low_confidence)


class ExtractPdfTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_text_layer(self):
        pdf = self.tmp / "text.pdf"
        _build_text_pdf(pdf)

        text = pdf_extract.extract_pdf_text(pdf)

        self.assertIsNotNone(text)
        self.assertIn("clean markdown extraction", text)

    def test_image_only_pdf_has_no_text(self):
        pdf = self.tmp / "scanned.pdf"
        _build_image_only_pdf(pdf)

        text = pdf_extract.extract_pdf_text(pdf)

        self.assertIsNotNone(text)
        self.assertEqual(text.strip(), "")


class AutoTierCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "paper.pdf"
        make_pdf(self.pdf)

        self.work = self.tmp / "work"

        def fake_mkdtemp(prefix=None, dir=None):
            self.work.mkdir(parents=True, exist_ok=True)
            return str(self.work)

        self.mkdtemp_patch = mock.patch.object(tempfile, "mkdtemp", side_effect=fake_mkdtemp)
        self.mkdtemp_patch.start()

    def tearDown(self):
        self.mkdtemp_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pdf_extract.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_auto_routes_formula_pdf_to_standard_and_prints_reason(self):
        formula = "The variables α β γ δ τ σ μ λ and operators ∑ ∫ ≤ ≥ ∞ appear. " * 20
        with mock.patch.object(pdf_extract, "extract_pdf_text", return_value=formula), \
             mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, out, _ = self.run_main([str(self.pdf)])

        self.assertEqual(code, 0)
        self.assertIn("standard", out)
        self.assertIn("formula", out.lower())

    def test_forced_tier_is_passed_through(self):
        seen = {}

        def recording_runner(pdf, zip_path, tier, ocr_mode, timeout):
            seen["tier"] = tier
            seen["ocr_mode"] = ocr_mode
            make_runner()(pdf, zip_path, tier, ocr_mode, timeout)

        with mock.patch.object(
            pdf_extract, "extract_pdf_text", side_effect=AssertionError("must not read")
        ), mock.patch.object(pdf_extract, "run_mineru", recording_runner):
            code, out, _ = self.run_main(
                [str(self.pdf), "--tier", "flash", "--ocr-mode", "ocr"]
            )

        self.assertEqual(code, 0)
        self.assertEqual(seen, {"tier": "flash", "ocr_mode": "ocr"})
        self.assertIn("forced", out.lower())

    def test_json_flags_low_confidence(self):
        weak = plain_text_repetitions(100) + " α β γ δ ε ζ η θ ι κ"
        with mock.patch.object(pdf_extract, "extract_pdf_text", return_value=weak), \
             mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, out, _ = self.run_main([str(self.pdf), "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["any_low_confidence"])
        self.assertTrue(payload["results"][0]["low_confidence"])
        self.assertEqual(payload["results"][0]["tier"], "flash")

    def test_json_confident_run_has_no_low_confidence(self):
        with mock.patch.object(
            pdf_extract, "extract_pdf_text", return_value=plain_text_repetitions()
        ), mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, out, _ = self.run_main([str(self.pdf), "--json"])

        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertFalse(payload["any_low_confidence"])
        self.assertFalse(payload["results"][0]["low_confidence"])

    def test_json_argument_error_is_machine_readable(self):
        code, out, _ = self.run_main([str(self.tmp / "nope.pdf"), "--json"])

        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["results"], [])
        self.assertIn("not found", payload["errors"][0]["message"].lower())


def _build_text_pdf(path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    width, height = letter
    doc = canvas.Canvas(str(path), pagesize=letter)
    doc.setFont("Helvetica", 12)
    for index in range(10):
        doc.drawString(54, height - 60 - index * 18, "A study of clean markdown extraction")
    doc.save()


def _build_image_only_pdf(path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    # A text-free page: just a filled rectangle, no font is drawn.
    width, height = letter
    doc = canvas.Canvas(str(path), pagesize=letter)
    doc.setFillColorRGB(0.2, 0.3, 0.4)
    doc.rect(50, 50, width - 100, height - 100, fill=1, stroke=0)
    doc.save()


if __name__ == "__main__":
    unittest.main()
