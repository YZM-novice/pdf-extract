"""Codex adaptation: the shell hook and the skill UI metadata.

Codex's ``PreToolUse`` hook fires for shell commands only, and its event JSON is
not stable, so the hook reads the raw payload and matches keywords. These tests
pin the allow/block contract: the pipeline itself must always pass, while a
shell command that touches a PDF or the MinerU temp tree is blocked (exit 2).
"""

import pathlib
import subprocess
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "codex" / "block-pdf-reads.sh"
OPENAI_YAML = REPO_ROOT / "agents" / "openai.yaml"


def run_hook(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
    )


class BlockPdfReadsHookTest(unittest.TestCase):
    def test_allows_the_pdf_extract_command_line(self):
        self.assertEqual(run_hook('{"command":"pdf-extract paper.pdf"}').returncode, 0)

    def test_allows_the_bundled_script(self):
        self.assertEqual(
            run_hook('{"command":"python /x/pdf_extract.py paper.pdf"}').returncode,
            0,
        )

    def test_blocks_reading_a_pdf(self):
        result = run_hook('{"command":"cat /work/paper.pdf"}')
        self.assertEqual(result.returncode, 2)
        self.assertIn("blocked", result.stderr.lower())

    def test_blocks_the_temp_tree(self):
        self.assertEqual(
            run_hook('{"command":"ls /tmp/mineru-pdf-extract-abc"}').returncode, 2
        )
        self.assertEqual(
            run_hook('{"command":"cat /tmp/mineru-pdf-extract-abc/markdown.md"}').returncode,
            2,
        )

    def test_blocks_tmpdir_references(self):
        self.assertEqual(run_hook('{"command":"echo $TMPDIR"}').returncode, 2)

    def test_allows_unrelated_commands(self):
        self.assertEqual(run_hook('{"command":"ls -la && git status"}').returncode, 0)


class OpenAiMetadataTest(unittest.TestCase):
    def test_enables_implicit_invocation(self):
        text = OPENAI_YAML.read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: true", text)
        self.assertIn("display_name:", text)
        self.assertIn("default_prompt:", text)


if __name__ == "__main__":
    unittest.main()
