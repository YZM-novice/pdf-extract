"""Ticket 02: multi-PDF batch conversion, `--out` root, and force semantics."""

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


def make_pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 fake")


def make_runner(fail_for=()):
    fail_for = {str(p) for p in fail_for}

    def runner(pdf, zip_path, tier, ocr_mode, timeout):
        if str(pdf) in fail_for:
            raise pdf_extract.ConversionError(f"boom for {pdf}")
        write_fake_zip(zip_path, f"# Body of {pathlib.Path(pdf).stem}\n", {"fig.jpg": b"x"})

    return runner


class BatchTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
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


class BatchOutputLocationTest(BatchTestBase):
    def test_each_pdf_gets_sibling_dir(self):
        alpha = self.tmp / "alpha" / "a.pdf"
        beta = self.tmp / "beta" / "b.pdf"
        make_pdf(alpha)
        make_pdf(beta)

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, _, _ = self.run_main([str(alpha), str(beta)])

        self.assertEqual(code, 0)
        self.assertTrue((alpha.parent / "a_md" / "cleaned.md").exists())
        self.assertTrue((beta.parent / "b_md" / "cleaned.md").exists())

    def test_out_flag_unifies_output_root(self):
        alpha = self.tmp / "alpha" / "a.pdf"
        beta = self.tmp / "beta" / "b.pdf"
        make_pdf(alpha)
        make_pdf(beta)
        root = self.tmp / "out"

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, _, _ = self.run_main([str(alpha), str(beta), "--out", str(root)])

        self.assertEqual(code, 0)
        self.assertTrue((root / "a_md" / "cleaned.md").exists())
        self.assertTrue((root / "b_md" / "cleaned.md").exists())
        self.assertFalse((alpha.parent / "a_md").exists())

    def test_batch_without_out_prints_location_reminder(self):
        first = self.tmp / "a.pdf"
        second = self.tmp / "b.pdf"
        make_pdf(first)
        make_pdf(second)

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, out, _ = self.run_main([str(first), str(second)])

        self.assertEqual(code, 0)
        self.assertIn("no --out given", out)

    def test_duplicate_stems_with_out_are_disambiguated(self):
        first = self.tmp / "one" / "same.pdf"
        second = self.tmp / "two" / "same.pdf"
        make_pdf(first)
        make_pdf(second)
        root = self.tmp / "out"

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, _, _ = self.run_main([str(first), str(second), "--out", str(root)])

        self.assertEqual(code, 0)
        self.assertTrue((root / "same_1_md" / "cleaned.md").exists())
        self.assertTrue((root / "same_2_md" / "cleaned.md").exists())
        self.assertTrue((root / "same_1_md" / "image").is_dir())
        self.assertTrue((root / "same_2_md" / "image").is_dir())


class NameFlagTest(BatchTestBase):
    def test_name_sets_container(self):
        pdf = self.tmp / "long-paper-name.pdf"
        make_pdf(pdf)

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, _, _ = self.run_main([str(pdf), "--name", "2"])

        self.assertEqual(code, 0)
        container = self.tmp / "2_md"
        self.assertTrue((container / "cleaned.md").exists())
        self.assertTrue((container / "image").is_dir())

    def test_md_suffix_on_name_is_stripped(self):
        pdf = self.tmp / "a.pdf"
        make_pdf(pdf)

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, _, _ = self.run_main([str(pdf), "--name", "2.md"])

        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "2_md" / "cleaned.md").exists())

    def test_batch_takes_one_name_per_pdf(self):
        first = self.tmp / "a.pdf"
        second = self.tmp / "b.pdf"
        make_pdf(first)
        make_pdf(second)
        root = self.tmp / "out"

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, _, _ = self.run_main(
                [str(first), str(second), "--out", str(root), "--name", "1", "--name", "2"]
            )

        self.assertEqual(code, 0)
        self.assertTrue((root / "1_md" / "cleaned.md").exists())
        self.assertTrue((root / "2_md" / "cleaned.md").exists())
        self.assertTrue((root / "1_md" / "image").is_dir())
        self.assertTrue((root / "2_md" / "image").is_dir())

    def test_name_count_mismatch_errors_with_fix_hint(self):
        first = self.tmp / "a.pdf"
        second = self.tmp / "b.pdf"
        make_pdf(first)
        make_pdf(second)
        runner = mock.Mock(side_effect=AssertionError("must not convert"))

        with mock.patch.object(pdf_extract, "run_mineru", runner):
            code, _, err = self.run_main([str(first), str(second), "--name", "2"])

        self.assertEqual(code, 1)
        self.assertIn("provide one --name per PDF", err)
        runner.assert_not_called()

    def test_name_with_path_separator_errors(self):
        pdf = self.tmp / "a.pdf"
        make_pdf(pdf)
        runner = mock.Mock(side_effect=AssertionError("must not convert"))

        with mock.patch.object(pdf_extract, "run_mineru", runner):
            code, _, err = self.run_main([str(pdf), "--name", "sub/2"])

        self.assertEqual(code, 1)
        self.assertIn("plain basename", err)
        runner.assert_not_called()

    def test_duplicate_input_pdf_errors(self):
        pdf = self.tmp / "a.pdf"
        make_pdf(pdf)
        runner = mock.Mock(side_effect=AssertionError("must not convert"))

        with mock.patch.object(pdf_extract, "run_mineru", runner):
            code, _, err = self.run_main([str(pdf), str(pdf)])

        self.assertEqual(code, 1)
        self.assertIn("duplicate input", err.lower())
        runner.assert_not_called()


class ForceTest(BatchTestBase):
    def test_existing_target_without_force_errors_before_converting(self):
        first = self.tmp / "a.pdf"
        second = self.tmp / "b.pdf"
        make_pdf(first)
        make_pdf(second)
        (first.parent / "a_md").mkdir()
        runner = mock.Mock(side_effect=AssertionError("must not convert"))

        with mock.patch.object(pdf_extract, "run_mineru", runner):
            code, _, err = self.run_main([str(first), str(second)])

        self.assertEqual(code, 1)
        self.assertIn("exists", err.lower())
        runner.assert_not_called()

    def test_force_overwrites_existing_target(self):
        pdf = self.tmp / "a.pdf"
        make_pdf(pdf)
        target = self.tmp / "a_md"
        target.mkdir()
        stale = target / "stale.txt"
        stale.write_text("old")

        with mock.patch.object(pdf_extract, "run_mineru", make_runner()):
            code, _, _ = self.run_main([str(pdf), "--force"])

        self.assertEqual(code, 0)
        self.assertFalse(stale.exists())
        self.assertTrue((target / "cleaned.md").exists())


class BatchFailureTest(BatchTestBase):
    def test_continues_after_failure_and_returns_first_failure_code(self):
        first = self.tmp / "a.pdf"
        second = self.tmp / "b.pdf"
        third = self.tmp / "c.pdf"
        make_pdf(first)
        make_pdf(second)
        make_pdf(third)

        runner = make_runner(fail_for=[second])
        with mock.patch.object(pdf_extract, "run_mineru", runner):
            code, _, err = self.run_main([str(first), str(second), str(third)])

        self.assertEqual(code, 2)
        self.assertTrue((first.parent / "a_md" / "cleaned.md").exists())
        self.assertTrue((third.parent / "c_md" / "cleaned.md").exists())
        self.assertFalse((second.parent / "b_md" / "cleaned.md").exists())
        self.assertIn(str(second), err)


if __name__ == "__main__":
    unittest.main()
