"""MinerU / Python path resolution (public release, handoff §3.8).

An installed copy ships a pinned ``pdf-extract.env`` so it needs no environment;
a source checkout falls back to the env override, then PATH, then a clear error.
Nothing is allowed to hard-code a machine-specific path.
"""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import pdf_extract  # noqa: E402


class ResolveMineruBinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.config = self.tmp / "pdf-extract.env"
        self.pinned = self.tmp / "pinned" / "mineru-kit"
        self.env_bin = self.tmp / "env" / "mineru-kit"
        for path in (self.pinned, self.env_bin):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n", encoding="utf-8")
        self.base_env = {
            "PDF_EXTRACT_CONFIG": str(self.config),
            "PDF_EXTRACT_MINERU_BIN": str(self.env_bin),
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _resolve(self, *, env=None, which=None):
        env = self.base_env if env is None else env
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(pdf_extract.shutil, "which", return_value=which):
                return pdf_extract.resolve_mineru_bin()

    def test_pinned_config_wins_over_env_and_path(self):
        self.config.write_text(f"MINERU_BIN={self.pinned}\n", encoding="utf-8")
        self.assertEqual(self._resolve(which="/path/mineru-kit"), str(self.pinned))

    def test_env_used_when_no_pinned_config(self):
        self.assertEqual(self._resolve(which="/path/mineru-kit"), str(self.env_bin))

    def test_path_used_when_no_config_and_no_env(self):
        env = {"PDF_EXTRACT_CONFIG": str(self.config)}
        self.assertEqual(self._resolve(env=env, which="/path/mineru-kit"), "/path/mineru-kit")

    def test_missing_pinned_file_falls_through_to_env(self):
        self.config.write_text(
            f"MINERU_BIN={self.tmp / 'gone' / 'mineru-kit'}\n", encoding="utf-8"
        )
        self.assertEqual(self._resolve(), str(self.env_bin))

    def test_explicit_error_when_nothing_is_found(self):
        env = {"PDF_EXTRACT_CONFIG": str(self.config)}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(pdf_extract.shutil, "which", return_value=None):
                with self.assertRaises(pdf_extract.ConversionError):
                    pdf_extract.resolve_mineru_bin()


class ResolvePythonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.config = self.tmp / "pdf-extract.env"
        self.sibling = self.tmp / "env" / "python"
        self.sibling.parent.mkdir(parents=True, exist_ok=True)
        self.sibling.write_text("#!/bin/sh\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_override_wins(self):
        env = {"PDF_EXTRACT_PYTHON": "/custom/python"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(pdf_extract.resolve_python(), "/custom/python")

    def test_pinned_config_used(self):
        self.config.write_text(f"MINERU_PYTHON={self.sibling}\n", encoding="utf-8")
        env = {"PDF_EXTRACT_CONFIG": str(self.config)}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.dict(os.environ, {"PDF_EXTRACT_PYTHON": ""}, clear=False):
                self.assertEqual(pdf_extract.resolve_python(), str(self.sibling))

    def test_derives_from_mineru_sibling(self):
        env = {
            "PDF_EXTRACT_CONFIG": str(self.config),
            "PDF_EXTRACT_MINERU_BIN": str(self.sibling.parent / "mineru-kit"),
        }
        (self.sibling.parent / "mineru-kit").write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.dict(os.environ, {"PDF_EXTRACT_PYTHON": ""}, clear=False):
                self.assertEqual(pdf_extract.resolve_python(), str(self.sibling))

    def test_falls_back_to_python3(self):
        env = {"PDF_EXTRACT_CONFIG": str(self.config)}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.dict(os.environ, {"PDF_EXTRACT_PYTHON": ""}, clear=False):
                with mock.patch.object(pdf_extract.shutil, "which", return_value=None):
                    self.assertEqual(pdf_extract.resolve_python(), "python3")


if __name__ == "__main__":
    unittest.main()
