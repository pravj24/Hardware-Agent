"""Opt-in integration against the actual pinned DMTF server, not our test double."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

from overwatch.redfish import RedfishClient, RedfishError


@unittest.skipUnless(os.environ.get("WATCHER_DMTF_REPO"), "set WATCHER_DMTF_REPO for upstream integration")
class DMTFIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(os.environ["WATCHER_DMTF_REPO"]).resolve()
        cls.temp = tempfile.TemporaryDirectory()
        cls.log = open(Path(cls.temp.name) / "dmtf.log", "w+")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        command = [os.environ.get("WATCHER_DMTF_PYTHON", "python"), str(cls.repo / "redfishMockupServer.py"),
                   "-H", "127.0.0.1", "-p", str(port), "-D", str(cls.repo / "public-rackmount1"), "-S"]
        cls.process = subprocess.Popen(command, cwd=cls.repo, stdout=cls.log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        cls.client = RedfishClient(f"http://127.0.0.1:{port}", timeout=1)
        try:
            for _ in range(60):
                if cls.process.poll() is not None:
                    raise RuntimeError("DMTF process stopped; check dependency installation")
                try:
                    cls.client.get("/redfish/v1/")
                    return
                except RedfishError:
                    time.sleep(0.1)
            raise RuntimeError("DMTF server did not become ready")
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if os.name == "nt" and cls.process.poll() is None:
            # Windows virtualenv redirectors can create a child interpreter.
            # Kill only this test-owned process tree, not all Python processes.
            subprocess.run(["taskkill", "/PID", str(cls.process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        elif cls.process.poll() is None:
            cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        cls.temp.cleanup()

    def test_upstream_commit_matches_manifest(self):
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        manifest = json.loads((Path(__file__).parents[1] / "integrations/dmtf/manifest.json").read_text())
        self.assertEqual(actual, manifest["commit"])

    def test_collects_published_rackmount_fixture(self):
        readings = self.client.collect()
        self.assertEqual(readings[0]["asset_id"], "437XR1138R2")
        self.assertIsInstance(readings[0]["temperature"], (float, int))
        self.assertEqual(readings[0]["timestamp_basis"], "retrieval_only")

    def test_upstream_action_acceptance_does_not_prove_power_state_changed(self):
        system = self.client.members(self.client.get("/redfish/v1/")["Systems"])[0]
        action = system["Actions"]["#ComputerSystem.Reset"]["target"]
        self.client.request("POST", action, {"ResetType": "ForceOff"})
        reread = self.client.get(system["@odata.id"])
        self.assertEqual(reread["PowerState"], system["PowerState"])
