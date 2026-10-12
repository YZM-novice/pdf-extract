"""Ticket 04: shared MinerU api-server lifecycle for batch standard conversion."""

import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import mineru_server  # noqa: E402
from scripts import pdf_extract  # noqa: E402
from scripts.mineru_server import MineruApiServer, MineruServerError  # noqa: E402
from tests.test_batch import make_pdf  # noqa: E402
from tests.test_convert import write_fake_zip  # noqa: E402

FORMULA_TEXT = "The variables α β γ δ τ σ μ λ and operators ∑ ∫ ≤ ≥ ∞ appear. " * 20
PLAIN_TEXT = "reliability analysis and maintenance planning. " * 200


def dead_pid() -> int:
    """A pid that is guaranteed not to exist (reaped by wait)."""
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.waited = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.waited = True
        return 0


class SsParsingTest(unittest.TestCase):
    SS = (
        "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
        "LISTEN 0      2048   127.0.0.1:18765    0.0.0.0:*\n"
        "LISTEN 0      4096   127.0.0.1:38408    0.0.0.0:*\n"
    )

    def test_finds_listening_port(self):
        self.assertTrue(mineru_server.ss_reports_listen(self.SS, 18765))

    def test_ignores_other_ports(self):
        self.assertFalse(mineru_server.ss_reports_listen(self.SS, 9999))

    def test_header_only(self):
        self.assertFalse(mineru_server.ss_reports_listen("State Recv-Q\n", 18765))

    def test_ipv6_form(self):
        output = "LISTEN 0 128 [::1]:18765 [::]:*\n"
        self.assertTrue(mineru_server.ss_reports_listen(output, 18765))


class PidFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.pid_file = self.tmp / "srv.pid"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        mineru_server.write_pid_file(self.pid_file, 4242, 18765)
        self.assertEqual(mineru_server.read_pid_file(self.pid_file), 4242)

    def test_missing_and_malformed_return_none(self):
        self.assertIsNone(mineru_server.read_pid_file(self.tmp / "nope.pid"))
        self.pid_file.write_text("not json")
        self.assertIsNone(mineru_server.read_pid_file(self.pid_file))

    def test_remove_is_idempotent(self):
        mineru_server.remove_pid_file(self.pid_file)
        self.assertFalse(self.pid_file.exists())

    def test_pid_file_path_is_per_port(self):
        self.assertEqual(mineru_server.pid_file_path(18765).name, "api-server-18765.pid")
        self.assertNotEqual(mineru_server.pid_file_path(1), mineru_server.pid_file_path(2))


class ServerStartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.pid_file = self.tmp / "srv.pid"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _server(self, **overrides):
        options = dict(
            port=18765,
            pid_file=self.pid_file,
            port_probe=lambda port: False,
            health_check=lambda url: True,
            sleep=lambda _seconds: None,
        )
        options.update(overrides)
        return MineruApiServer("mineru-kit", **options)

    def test_port_free_spawns_server_and_writes_pid(self):
        process = FakeProcess(dead_pid())
        popen = mock.Mock(return_value=process)
        server = self._server(popen=popen)

        server.start()

        self.assertFalse(server.reused)
        self.assertTrue(popen.called)
        _, kwargs = popen.call_args
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(mineru_server.read_pid_file(self.pid_file), process.pid)

    def test_occupied_by_foreign_process_refuses_and_does_not_spawn(self):
        popen = mock.Mock(side_effect=AssertionError("must not spawn"))
        server = self._server(port_probe=lambda port: True, popen=popen)

        with self.assertRaises(MineruServerError):
            server.start()

        popen.assert_not_called()

    def test_reuses_owned_healthy_server(self):
        mineru_server.write_pid_file(self.pid_file, os.getpid(), 18765)
        popen = mock.Mock(side_effect=AssertionError("must not spawn"))
        server = self._server(port_probe=lambda port: True, popen=popen)

        with mock.patch.object(mineru_server, "pid_is_mineru_api_server", return_value=True):
            server.start()

        self.assertTrue(server.reused)
        popen.assert_not_called()
        server.stop()
        self.assertTrue(self.pid_file.exists())  # reused server's marker is left alone

    def test_stale_pid_is_cleared_before_start(self):
        stale = dead_pid()
        mineru_server.write_pid_file(self.pid_file, stale, 18765)
        process = FakeProcess(dead_pid())
        while process.pid == stale:
            process = FakeProcess(dead_pid())
        server = self._server(popen=mock.Mock(return_value=process))

        server.start()

        self.assertEqual(mineru_server.read_pid_file(self.pid_file), process.pid)

    def test_foreign_live_pid_is_dropped_without_killing(self):
        mineru_server.write_pid_file(self.pid_file, os.getpid(), 18765)
        server = self._server(port_probe=lambda port: True)

        with mock.patch.object(
            mineru_server, "pid_is_mineru_api_server", return_value=False
        ):
            with self.assertRaises(MineruServerError):
                server.start()

        self.assertFalse(self.pid_file.exists())
        self.assertTrue(mineru_server.pid_is_alive(os.getpid()))

    def test_unhealthy_server_is_stopped_and_raises(self):
        process = FakeProcess(dead_pid())
        server = self._server(
            health_check=lambda url: False,
            startup_timeout=0.01,
            popen=mock.Mock(return_value=process),
        )

        with self.assertRaises(MineruServerError):
            server.start()

        self.assertFalse(self.pid_file.exists())

    def test_stop_kills_process_group_with_no_leftovers(self):
        server = self._server(command=["sleep", "300"])
        server.start()
        pid = mineru_server.read_pid_file(self.pid_file)
        self.assertIsNotNone(pid)
        pgid = os.getpgid(pid)

        server.stop()

        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)
        self.assertFalse(self.pid_file.exists())


class RemoteRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "a.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fake")
        self.zip = self.tmp / "out.zip"
        self.mineru_patch = mock.patch.object(
            pdf_extract, "mineru_bin", return_value="/fake/mineru-kit"
        )
        self.mineru_patch.start()

    def tearDown(self):
        self.mineru_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_mineru_uses_remote_url(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            self.zip.write_bytes(b"zip")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(pdf_extract.subprocess, "run", fake_run):
            pdf_extract.run_mineru(
                self.pdf, self.zip, "standard", "txt", 5, remote_url="http://127.0.0.1:18765"
            )

        command = captured["command"]
        self.assertIn("--remote-url", command)
        self.assertIn("http://127.0.0.1:18765", command)
        self.assertIn("--tier", command)
        self.assertIn("standard", command)

    def test_remote_runner_binds_url(self):
        with mock.patch.object(pdf_extract, "run_mineru") as run:
            runner = pdf_extract.make_remote_runner("http://127.0.0.1:18765")
            runner(self.pdf, self.zip, "standard", "txt", 5)

        run.assert_called_once_with(
            self.pdf, self.zip, "standard", "txt", 5,
            remote_url="http://127.0.0.1:18765",
        )


class FakeServer:
    instances: list["FakeServer"] = []

    def __init__(self, bin_path, **kwargs):
        self.started = False
        self.stopped = False
        self.reused = False
        FakeServer.instances.append(self)

    @property
    def remote_url(self):
        return "http://127.0.0.1:18765"

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class ServerCliTest(unittest.TestCase):
    def setUp(self):
        FakeServer.instances = []
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.work = self.tmp / "work"

        def fake_mkdtemp(prefix=None, dir=None):
            self.work.mkdir(parents=True, exist_ok=True)
            return str(self.work)

        self.mkdtemp_patch = mock.patch.object(tempfile, "mkdtemp", side_effect=fake_mkdtemp)
        self.mkdtemp_patch.start()
        # Batch standard needs a mineru-kit path; the tests never run the real
        # binary, so resolve it to a stable fake.
        self.mineru_patch = mock.patch.object(
            pdf_extract, "mineru_bin", return_value="/fake/mineru-kit"
        )
        self.mineru_patch.start()

    def tearDown(self):
        self.mineru_patch.stop()
        self.mkdtemp_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = pdf_extract.main(args)
        return code, out.getvalue(), err.getvalue()

    def _pdf(self, name):
        pdf = self.tmp / name
        make_pdf(pdf)
        return pdf

    def test_batch_standard_starts_one_server_and_parses_all_remotely(self):
        first, second = self._pdf("a.pdf"), self._pdf("b.pdf")
        calls = []

        def fake_mineru(pdf, zip_path, tier, ocr_mode, timeout, remote_url=None):
            calls.append((str(pdf), tier, remote_url))
            write_fake_zip(zip_path, "Body.\n", {"fig.jpg": b"x"})

        with mock.patch.object(pdf_extract, "MineruApiServer", FakeServer), \
             mock.patch.object(pdf_extract, "extract_pdf_text", return_value=FORMULA_TEXT), \
             mock.patch.object(pdf_extract, "run_mineru", side_effect=fake_mineru):
            code, out, _ = self.run_main([str(first), str(second)])

        self.assertEqual(code, 0)
        self.assertEqual(len(FakeServer.instances), 1)
        self.assertTrue(FakeServer.instances[0].started)
        self.assertTrue(FakeServer.instances[0].stopped)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(url == "http://127.0.0.1:18765" for _, _, url in calls))
        self.assertIn("api-server started", out)

    def test_single_standard_pdf_does_not_start_a_server(self):
        pdf = self._pdf("a.pdf")
        calls = []

        def fake_mineru(pdf, zip_path, tier, ocr_mode, timeout, remote_url=None):
            calls.append(remote_url)
            write_fake_zip(zip_path, "Body.\n", {"fig.jpg": b"x"})

        with mock.patch.object(pdf_extract, "MineruApiServer", FakeServer), \
             mock.patch.object(pdf_extract, "extract_pdf_text", return_value=FORMULA_TEXT), \
             mock.patch.object(pdf_extract, "run_mineru", side_effect=fake_mineru):
            code, _, _ = self.run_main([str(pdf)])

        self.assertEqual(code, 0)
        self.assertEqual(FakeServer.instances, [])
        self.assertEqual(calls, [None])

    def test_batch_plain_text_does_not_start_a_server(self):
        first, second = self._pdf("a.pdf"), self._pdf("b.pdf")
        calls = []

        def fake_mineru(pdf, zip_path, tier, ocr_mode, timeout, remote_url=None):
            calls.append(remote_url)
            write_fake_zip(zip_path, "Body.\n", {"fig.jpg": b"x"})

        with mock.patch.object(pdf_extract, "MineruApiServer", FakeServer), \
             mock.patch.object(pdf_extract, "extract_pdf_text", return_value=PLAIN_TEXT), \
             mock.patch.object(pdf_extract, "run_mineru", side_effect=fake_mineru):
            code, _, _ = self.run_main([str(first), str(second)])

        self.assertEqual(code, 0)
        self.assertEqual(FakeServer.instances, [])
        self.assertEqual(calls, [None, None])

    def test_mixed_batch_sends_standard_and_flash_remotely(self):
        standard_pdf, flash_pdf = self._pdf("std.pdf"), self._pdf("plain.pdf")
        calls = []

        def fake_mineru(pdf, zip_path, tier, ocr_mode, timeout, remote_url=None):
            calls.append((pathlib.Path(pdf).name, tier, remote_url))
            write_fake_zip(zip_path, "Body.\n", {"fig.jpg": b"x"})

        def routed_text(pdf):
            return FORMULA_TEXT if "std" in str(pdf) else PLAIN_TEXT

        with mock.patch.object(pdf_extract, "MineruApiServer", FakeServer), \
             mock.patch.object(pdf_extract, "extract_pdf_text", side_effect=routed_text), \
             mock.patch.object(pdf_extract, "run_mineru", side_effect=fake_mineru):
            code, _, _ = self.run_main([str(standard_pdf), str(flash_pdf)])

        self.assertEqual(code, 0)
        self.assertEqual(len(FakeServer.instances), 1)
        self.assertEqual(
            calls,
            [
                ("std.pdf", "standard", "http://127.0.0.1:18765"),
                ("plain.pdf", "flash", "http://127.0.0.1:18765"),
            ],
        )

    def test_server_start_failure_returns_2_and_converts_nothing(self):
        first, second = self._pdf("a.pdf"), self._pdf("b.pdf")

        class FailingServer(FakeServer):
            def start(self):
                raise MineruServerError("port 18765 is occupied")

        run = mock.Mock(side_effect=AssertionError("must not convert"))
        with mock.patch.object(pdf_extract, "MineruApiServer", FailingServer), \
             mock.patch.object(pdf_extract, "extract_pdf_text", return_value=FORMULA_TEXT), \
             mock.patch.object(pdf_extract, "run_mineru", run):
            code, _, err = self.run_main([str(first), str(second)])

        self.assertEqual(code, 2)
        self.assertIn("occupied", err)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
