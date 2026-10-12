"""Skill packaging (ticket 05 + public release): SKILL.md contract + installer.

There is no executable seam inside a skill, so the testable surface is:

* ``SKILL.md`` parses and carries the required frontmatter / instructions, and
* ``install.sh`` copies the skill (not symlinks) into the global skill dirs,
  writes an executable terminal entry, freezes its paths, and (for Codex)
  installs the skill metadata + shell hook.

The install targets are overridable via env vars so the test never writes into
the real ``$HOME``.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_SOURCE = REPO_ROOT / "SKILL.md"
INSTALL_SCRIPT = REPO_ROOT / "install.sh"
PYTHON = sys.executable


def _frontmatter(text):
    """Parse the simple ``key: value`` frontmatter without a YAML dependency."""
    if not text.startswith("---\n"):
        return {}
    block = text.split("---\n", 2)[1]
    fields = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"')
    return fields


class SkillDocumentTest(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_SOURCE.read_text(encoding="utf-8")
        self.lowered = self.text.lower()
        self.front = _frontmatter(self.text)

    def test_named_pdf_extract_and_implicitly_invocable(self):
        self.assertEqual(self.front["name"], "pdf-extract")
        self.assertNotIn("disable-model-invocation", self.front)
        self.assertTrue(self.front.get("description"))

    def test_description_says_when_to_use(self):
        description = self.front["description"].lower()
        self.assertIn("whenever", description)
        self.assertIn("pdf", description)

    def test_documents_both_harnesses(self):
        self.assertIn("pi", self.lowered)
        self.assertIn("codex", self.lowered)
        self.assertIn("scripts/pdf_extract.py", self.text)

    def test_forbids_reading_pdf_and_raw_artifacts(self):
        self.assertIn("never read the pdf", self.lowered)
        self.assertIn("raw", self.lowered)
        self.assertIn("temp", self.lowered)

    def test_reports_only_paths_and_stats(self):
        self.assertIn("stats", self.lowered)
        self.assertIn("artifact", self.lowered)

    def test_batch_without_location_confirms_first(self):
        self.assertIn("--out", self.text)
        self.assertIn("batch", self.lowered)
        self.assertIn("confirm", self.lowered)

    def test_low_confidence_asks_standard_or_flash(self):
        self.assertIn("low_confidence", self.lowered)
        self.assertIn("standard", self.lowered)
        self.assertIn("flash", self.lowered)

    def test_documents_container_naming(self):
        self.assertIn("cleaned.md", self.text)
        self.assertIn("image/", self.text)
        self.assertIn("--name", self.text)

    def test_explicit_fast_request_bypasses_auto_routing(self):
        self.assertIn('tier="flash"', self.text)
        self.assertIn('ocr_mode="txt"', self.text)
        self.assertIn("fast", self.lowered)

    def test_reuses_an_existing_cleaned_markdown(self):
        self.assertIn("cleaned.md", self.text)
        self.assertIn("cache", self.lowered)
        self.assertIn("reuse", self.lowered)

    def test_has_no_machine_specific_paths(self):
        self.assertNotIn("/home/", self.text)


class InstallScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.skill_dir = self.tmp / "skills" / "pdf-extract"
        self.codex_skill_dir = self.tmp / "codex-skills" / "pdf-extract"
        self.bin_dir = self.tmp / "bin"
        self.ext_dir = self.tmp / "extensions"
        self.codex_dir = self.tmp / "codex"
        self.mineru_bin = self.tmp / "mineru-kit"
        self.mineru_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.mineru_bin.chmod(0o755)
        self.result = self._run_install()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_install(self, *extra):
        env = os.environ.copy()
        env.update(
            {
                "PDF_EXTRACT_SKILL_DIR": str(self.skill_dir),
                "PDF_EXTRACT_CODEX_SKILL_DIR": str(self.codex_skill_dir),
                "PDF_EXTRACT_BIN_DIR": str(self.bin_dir),
                "PDF_EXTRACT_EXTENSION_DIR": str(self.ext_dir),
                "PDF_EXTRACT_CODEX_DIR": str(self.codex_dir),
                "PDF_EXTRACT_MINERU_BIN": str(self.mineru_bin),
                "PDF_EXTRACT_MINERU_PYTHON": PYTHON,
            }
        )
        args = ["--all", "--skills-only", *extra]
        return subprocess.run(
            ["bash", str(INSTALL_SCRIPT), *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_install_succeeds_and_copies_the_skill(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        for dest in (self.skill_dir, self.codex_skill_dir):
            self.assertEqual(
                (dest / "SKILL.md").read_text(encoding="utf-8"),
                SKILL_SOURCE.read_text(encoding="utf-8"),
            )
            for name in ("pdf_extract.py", "mineru_server.py"):
                self.assertTrue((dest / "scripts" / name).is_file())
            self.assertTrue((dest / "agents" / "openai.yaml").is_file())

    def test_install_is_copy_not_symlink(self):
        self.assertFalse((self.skill_dir / "SKILL.md").is_symlink())
        self.assertFalse((self.skill_dir / "scripts" / "pdf_extract.py").is_symlink())

    def test_freezes_resolved_paths(self):
        config = (self.skill_dir / "pdf-extract.env").read_text(encoding="utf-8")
        self.assertIn(f"MINERU_BIN={self.mineru_bin}", config)
        self.assertIn(f"MINERU_PYTHON={PYTHON}", config)

    def test_installs_an_executable_terminal_entry(self):
        entry = self.bin_dir / "pdf-extract"
        self.assertTrue(entry.is_file())
        self.assertTrue(os.access(entry, os.X_OK))
        body = entry.read_text(encoding="utf-8")
        self.assertIn("pdf_extract.py", body)
        self.assertIn(PYTHON, body)

    def test_reinstall_overwrites_stale_copies(self):
        (self.skill_dir / "SKILL.md").write_text("stale", encoding="utf-8")
        result = self._run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.skill_dir / "SKILL.md").read_text(encoding="utf-8"),
            SKILL_SOURCE.read_text(encoding="utf-8"),
        )

    def test_install_copies_the_pi_extension(self):
        source = REPO_ROOT / "extensions" / "mineru-pdf-extract.ts"
        installed = self.ext_dir / "mineru-pdf-extract.ts"
        self.assertEqual(installed.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
        self.assertFalse(installed.is_symlink())

    def test_installs_codex_hook_and_enables_the_feature(self):
        hook = self.codex_dir / "hooks" / "block-pdf-reads.sh"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(hook, os.X_OK))
        import json

        hooks = json.loads((self.codex_dir / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            item["command"]
            for entry in hooks["hooks"]["PreToolUse"]
            for item in entry["hooks"]
        ]
        self.assertIn(str(hook), commands)
        config = (self.codex_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn("[features]", config)
        self.assertIn("codex_hooks = true", config)

    def test_codex_merge_preserves_existing_config_and_hooks(self):
        import json

        self.codex_dir.mkdir(parents=True, exist_ok=True)
        (self.codex_dir / "config.toml").write_text('model = "x"\n', encoding="utf-8")
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "/other/hook.sh"}],
                    }
                ]
            }
        }
        (self.codex_dir / "hooks.json").write_text(
            json.dumps(existing), encoding="utf-8"
        )

        result = self._run_install()
        self.assertEqual(result.returncode, 0, result.stderr)

        config = (self.codex_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "x"', config)
        self.assertIn("codex_hooks = true", config)
        hooks = json.loads((self.codex_dir / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            item["command"]
            for entry in hooks["hooks"]["PreToolUse"]
            for item in entry["hooks"]
        ]
        self.assertIn("/other/hook.sh", commands)
        self.assertIn(str(self.codex_dir / "hooks" / "block-pdf-reads.sh"), commands)

    def test_no_codex_hooks_skips_the_hook(self):
        shutil.rmtree(self.codex_dir, ignore_errors=True)
        result = self._run_install("--no-codex-hooks")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.codex_dir / "hooks.json").exists())


class CodexOnlyInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.mineru_bin = self.tmp / "mineru-kit"
        self.mineru_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.mineru_bin.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_codex_only_does_not_touch_the_pi_extension(self):
        ext_dir = self.tmp / "extensions"
        env = os.environ.copy()
        env.update(
            {
                "PDF_EXTRACT_CODEX_SKILL_DIR": str(self.tmp / "codex-skill"),
                "PDF_EXTRACT_CODEX_DIR": str(self.tmp / "codex"),
                "PDF_EXTRACT_SKILL_DIR": str(self.tmp / "pi-skill"),
                "PDF_EXTRACT_BIN_DIR": str(self.tmp / "bin"),
                "PDF_EXTRACT_EXTENSION_DIR": str(ext_dir),
                "PDF_EXTRACT_MINERU_BIN": str(self.mineru_bin),
                "PDF_EXTRACT_MINERU_PYTHON": PYTHON,
            }
        )
        result = subprocess.run(
            ["bash", str(INSTALL_SCRIPT), "--codex", "--skills-only"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(ext_dir.exists())
        entry = (self.tmp / "bin" / "pdf-extract").read_text(encoding="utf-8")
        self.assertIn(str(self.tmp / "codex-skill"), entry)
        self.assertTrue((self.tmp / "codex-skill" / "scripts" / "pdf_extract.py").is_file())


if __name__ == "__main__":
    unittest.main()
