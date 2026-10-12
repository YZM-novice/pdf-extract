"""Bridge: run the Node behaviour tests for the pi extension (ticket 06).

The extension is TypeScript importing pi-only packages, so its executable
behaviour is tested with Node's built-in test runner in ``tests/js``. This keeps
the whole suite runnable through the repo's usual ``python -m unittest`` command.
"""

import pathlib
import shutil
import subprocess
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed")
class ExtensionBehaviourTest(unittest.TestCase):
    def test_node_extension_tests_pass(self):
        result = subprocess.run(
            [NODE, "--test", "tests/js/*.test.mjs"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
