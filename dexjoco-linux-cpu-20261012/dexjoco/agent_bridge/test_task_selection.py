"""Task selection unit tests, no simulator or SSH required."""
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
import unittest
from unittest.mock import patch
import bridge
from task_registry import TASKS, require_supported

class TaskSelectionTests(unittest.TestCase):
    def run_main(self, args, active):
        out = StringIO()
        with patch.object(sys, "argv", ["bridge.py", *args]), patch.object(bridge.Path, "exists", return_value=True), \
             patch.object(bridge, "request", return_value={"ok": True, "result": active}) as req, redirect_stdout(out):
            code = 0
            try:
                bridge.main()
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(out.getvalue()), req.call_count

    def test_catalog_requires_no_socket_or_simulator(self):
        out = StringIO()
        with patch.object(sys, "argv", ["bridge.py", "tasks"]), patch.object(bridge, "request") as req, redirect_stdout(out):
            bridge.main()
        result = json.loads(out.getvalue())["result"]
        self.assertEqual(len(result["tasks"]), 11)
        self.assertEqual(sum(t["bridge_supported"] for t in result["tasks"]), 11)
        req.assert_not_called()

    def test_omitted_task_reuses_any_active_task(self):
        active = {"task": "pick_bucket", "pid": 123, "episode_dir": "/existing"}
        code, result, count = self.run_main(["start"], active)
        self.assertEqual(code, 0)
        self.assertEqual(result["result"], active)
        self.assertEqual(count, 1)

    def test_matching_task_reuses_existing_episode(self):
        active = {"task": "water_plant", "pid": 123, "steps": 200}
        code, result, count = self.run_main(["start", "--task", "water_plant", "--seed", "7"], active)
        self.assertEqual(code, 0)
        self.assertEqual(result["result"], active)
        self.assertEqual(count, 1)

    def test_mismatch_rejected_without_reset(self):
        code, result, count = self.run_main(["start", "--task", "fold_glasses"], {"task": "click_mouse"})
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertIn("no reset was performed", result["error"])
        self.assertEqual(count, 1)

    def test_legacy_runner_not_silently_assumed(self):
        code, result, _ = self.run_main(["start", "--task", "click_mouse"], {"pid": 123})
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])

    def test_all_bimanual_supported_and_unknown_rejected(self):
        for name in TASKS:
            if name.startswith("bimanual_"):
                info = require_supported(name)
                self.assertEqual(info["native_action_dim"], 46)
                self.assertEqual(info["camera_keys"], ["ego", "wrist_left", "wrist_right"])
        with self.assertRaisesRegex(ValueError, "Unknown task"):
            require_supported("../evil")

if __name__ == "__main__":
    unittest.main()
