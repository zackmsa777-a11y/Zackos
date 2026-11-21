import runpy
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

power = runpy.run_path(str(Path(__file__).resolve().parents[1] / "power/zackos-power"))
command = power["command"]
submit = power["PowerServer"].request_power


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pending = Path(folder.name) / "pending.json"
        state = patch.dict(submit.__globals__, STATE=Path(folder.name), PENDING=self.pending)
        state.start()
        self.addCleanup(state.stop)
        self.server = SimpleNamespace(pending=None, issued=False)

    def test_scheduling_and_cancel(self):
        request = command("shutdown", ["-r", "+5"])
        self.assertEqual(request["action"], "reboot")
        self.assertAlmostEqual(request["at"], time.time() + 300, delta=2)
        submit(self.server, 0, request)
        self.assertTrue(self.pending.exists())
        self.assertEqual(submit(self.server, 0, {"action": "status"}), request)
        for _ in range(2):
            submit(self.server, 0, command("shutdown", ["-c"]))
            self.assertFalse(self.pending.exists())
            self.assertIsNone(self.server.pending)

    def test_desktop_user(self):
        for flags in (["-r", "+1"], ["-c"]):
            with self.assertRaises(ValueError):
                submit(self.server, 1001, command("shutdown", flags))
        request = command("shutdown", ["-h", "now"])
        self.assertIsNone(request["at"])
        self.assertEqual(submit(self.server, 1001, request)["action"], "poweroff")

    def test_invalid_requests(self):
        for request in ({"action": "no"}, {"action": "reboot", "at": True}):
            with self.assertRaises(ValueError):
                submit(self.server, 0, request)
        with self.assertRaises(ValueError):
            command("shutdown", ["-P", "25:10"])
        self.assertEqual(command("shutdown", ["-P", "23:55"])["action"], "poweroff")


if __name__ == "__main__":
    unittest.main()
