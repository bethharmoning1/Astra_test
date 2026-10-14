"""Check model access boundaries and ambiguous-action timeout handling."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from run_batch import CommandRecorder, InfrastructureError, RestrictedEpisode


class Recorder:
    def __init__(self, directory, observation):
        self.directory = directory
        self.observation = observation
        self.rows = []
        self.calls = []

    def append(self, row):
        self.rows.append(row)

    def call(self, op, *args, request=None, **kwargs):
        self.calls.append((op, args, request))
        return {"ok": True, "result": self.observation}


class AccessBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.episode = Path(self.temporary.name) / "episode"
        self.episode.mkdir()
        self.image = self.episode / "current.png"
        self.image.write_bytes(b"current image fixture")
        self.old_image = self.episode / "previous.png"
        self.old_image.write_bytes(b"previous image fixture")
        self.observation = {
            "episode_dir": str(self.episode), "step_count": 0, "action_calls": 0, "succeed": False,
            "terminated": False, "truncated": False, "images": {"front": str(self.image)},
            "contact_sheet": str(self.image),
        }
        self.recorder = Recorder(self.episode, self.observation)
        self.boundary = RestrictedEpisode(self.recorder, self.episode,
            {"native_action_dim": 23, "rotvec_action_dim": 22}, self.observation, 1000)

    def test_model_cannot_reset_or_read_files(self):
        for request in ({"op": "reset"}, {"op": "read_file", "path": str(self.old_image)},
                        {"op": "observe", "path": str(self.old_image)},
                        {"op": "move", "xyz": [0, 0, 1], "shell": "id"}):
            self.assertFalse(self.boundary.execute_tool(request)["ok"])
        self.assertEqual(self.recorder.calls, [])
        self.assertEqual(len(self.recorder.rows), 4)

    def test_image_loader_excludes_past_and_external_paths(self):
        self.assertEqual(self.boundary.image_loader(self.image)["data"], self.image.read_bytes())
        for path in (self.old_image, Path(self.temporary.name) / "secret.md"):
            with self.assertRaises(PermissionError):
                self.boundary.image_loader(path)

    def test_rejects_actions_exceeding_remaining_native_steps(self):
        self.observation["step_count"] = 990
        self.assertFalse(self.boundary.execute_tool({"op": "hold", "steps": 11})["ok"])
        self.assertFalse(self.boundary.execute_tool({"op": "act", "actions": [[0.0] * 23] * 11})["ok"])
        self.assertTrue(self.boundary.execute_tool({"op": "hold", "steps": 10})["ok"])
        self.assertEqual(len(self.recorder.calls), 1)

    def test_terminal_episode_cannot_advance(self):
        self.observation["terminated"] = True
        self.assertFalse(self.boundary.execute_tool({"op": "hold", "steps": 1})["ok"])
        self.assertEqual(self.recorder.calls, [])

    def test_episode_identity_cannot_change_silently(self):
        self.recorder.observation = {**self.observation, "episode_dir": str(self.episode.parent / "other")}
        with self.assertRaises(InfrastructureError):
            self.boundary.execute_tool({"op": "observe"})

    def test_timeout_is_logged_without_replaying_action(self):
        recorder = CommandRecorder(self.episode, self.episode / "runner.sock")
        with patch("run_batch.subprocess.run", side_effect=subprocess.TimeoutExpired("bridge", 210,
                output=b"partial output", stderr=b"diagnostic")) as run:
            with self.assertRaises(InfrastructureError):
                recorder.call("hold", "--steps", "30", request={"op": "hold", "steps": 30})
        self.assertEqual(run.call_count, 1)
        records = [json.loads(line) for line in recorder.path.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["exception"], "TimeoutExpired")
        self.assertEqual(records[0]["stdout"], "partial output")
        self.assertEqual(records[0]["stderr"], "diagnostic")
        self.assertEqual(recorder.error_count, 1)

    def test_partial_action_error_is_not_ordinary_validation_or_replayed(self):
        for steps, actions in ((1, 1), (0, 1)):
            self.boundary.latest = self.observation
            with patch.object(self.recorder, "call", side_effect=[
                    {"ok": False, "error": "execution failed"},
                    {"ok": True, "result": {**self.observation, "step_count": steps, "action_calls": actions}}]) as call:
                with self.assertRaises(InfrastructureError):
                    self.boundary.execute_tool({"op": "hold", "steps": 1})
            self.assertEqual([c.args[0] for c in call.call_args_list], ["hold", "observe"])

    def test_pre_action_rejection_can_be_corrected_only_after_state_check(self):
        with patch.object(self.recorder, "call", side_effect=[
                {"ok": False, "error": "joint limit validation"},
                {"ok": True, "result": self.observation}]) as call:
            result = self.boundary.execute_tool({"op": "hold", "steps": 1})
        self.assertFalse(result["ok"])
        self.assertEqual([c.args[0] for c in call.call_args_list], ["hold", "observe"])

    def test_observe_error_is_an_infrastructure_interruption(self):
        with patch.object(self.recorder, "call", return_value={"ok": False, "error": "render failed"}) as call:
            with self.assertRaises(InfrastructureError):
                self.boundary.execute_tool({"op": "observe"})
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
