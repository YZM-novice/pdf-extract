"""CLI exit-code contract: 1 argument/path, 2 conversion, 3 clean/write."""

import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import pdf_extract  # noqa: E402
from tests.test_convert import write_fake_zip  # noqa: E402


def run_main(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = pdf_extract.main(args)
    return code, out.getvalue(), err.getvalue()


class ExitCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.work = self.tmp / "work"

        def fake_mkdtemp(prefix=None, dir=None):
            self.work.mkdir(parents=True, exist_ok=True)
            return str(self.work)

        self.mkdtemp_patch = mock.patch.object(tempfile, "mkdtemp", side_effect=fake_mkdtemp)
        self.mkdtemp_patch.start()
        self.pdf = self.tmp / "paper.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fake")

    def tearDown(self):
        self.mkdtemp_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_pdf_returns_1(self):
        code, _, err = run_main([str(self.tmp / "nope.pdf")])
        self.assertEqual(code, 1)
        self.assertIn("not found", err.lower())

    def test_existing_output_without_force_returns_1(self):
        (self.tmp / "paper_md").mkdir()
        code, _, err = run_main([str(self.pdf)])
        self.assertEqual(code, 1)
        self.assertIn("exists", err.lower())

    def test_conversion_failure_returns_2(self):
        with mock.patch.object(
            pdf_extract, "run_mineru", side_effect=pdf_extract.ConversionError("mineru exploded")
        ):
            code, _, err = run_main([str(self.pdf)])
        self.assertEqual(code, 2)
        self.assertIn("mineru exploded", err)

    def test_write_failure_returns_3(self):
        with mock.patch.object(
            pdf_extract, "run_mineru", side_effect=pdf_extract.WriteError("disk full")
        ):
            code, _, err = run_main([str(self.pdf)])
        self.assertEqual(code, 3)
        self.assertIn("temp dir", err.lower())

    def test_success_returns_0_and_prints_tier_and_stats(self):
        def fake_runner(pdf, zip_path, tier, ocr_mode, timeout):
            write_fake_zip(zip_path, "Body.\n\n## References\n\n[1] Foo.\n", {"fig.jpg": b"x"})

        with mock.patch.object(pdf_extract, "run_mineru", fake_runner):
            code, out, _ = run_main([str(self.pdf)])

        self.assertEqual(code, 0)
        self.assertIn("flash", out)
        self.assertIn("removed", out)
        self.assertTrue((self.tmp / "paper_md" / "cleaned.md").exists())


if __name__ == "__main__":
    unittest.main()
