"""Critical isolation and action-once behavior, without a real model or robot."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.evaluation.model_runner import APIError, ModelRunner, parse_response_stream, validate_tool


JPEG = b"\xff\xd8\xff\xe0test-image-bytes\xff\xd9"
FAKE_KEY = "sk-unit-test-credential-never-valid"


def call(call_id="call-1", name="hold", args=None):
    return {"type": "function_call", "call_id": call_id, "name": name,
            "arguments": json.dumps({"steps": 1, "note": "test"} if args is None else args)}


def response(*calls):
    return {"id": "resp-test", "model": "gpt-6-astra", "status": "completed",
            "output": list(calls)}


class EpisodeHarness:
    def __init__(self, root, responses):
        self.root = root
        self.responses = iter(responses)
        self.requests = []
        self.tools = []
        self.loaded = []
        self.steps = 0
        self.obs_index = 0
        self.contract = {"task": "fixture_task", "native_action_dim": 23,
                         "rotvec_action_dim": 22, "camera_keys": ["front", "wrist"],
                         "object_truth": "MUST_NOT_REACH_MODEL"}
        self.context = {"task": "fixture_task", "seed": 0, "trial_index": 123,
                        "historical_results": "MUST_NOT_REACH_MODEL"}

    def observe(self):
        self.obs_index += 1
        folder = self.root / f"obs-{self.obs_index:04d}"
        return {"task": "fixture_task", "seed": 0, "episode_dir": str(self.root),
                "step_count": self.steps, "action_calls": len([t for t in self.tools if t["op"] != "observe"]),
                "robot_state": [0.0] * 23, "camera_keys": ["front", "wrist"],
                "images": {key: str(folder / f"{key}.png") for key in ("front", "wrist")},
                "contact_sheet": str(folder / "contact_sheet.jpg"),
                "succeed": False, "terminated": False, "truncated": False,
                "object_pose": "MUST_NOT_REACH_MODEL", "preview_token": "MUST_NOT_REACH_MODEL"}

    def load_image(self, path):
        self.loaded.append(path)
        return {"mime_type": "image/jpeg", "data": JPEG,
                "archive_path": Path(path).relative_to(self.root).as_posix(),
                "sha256": hashlib.sha256(JPEG).hexdigest()}

    def execute(self, payload):
        self.tools.append(copy.deepcopy(payload))
        if payload["op"] in ("hold", "move"):
            self.steps += payload["steps"]
        elif payload["op"] == "act":
            self.steps += len(payload["actions"])
        return {"ok": True, "result": self.observe()}

    def transport(self, request):
        self.requests.append(copy.deepcopy(request))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


class ModelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.credentials = self.root / "credential.md"
        self.credentials.write_text(FAKE_KEY)

    def tearDown(self):
        self.temporary.cleanup()

    def run_fixture(self, responses, *, limit=2, mutate=None, execute=None):
        harness = EpisodeHarness(self.root / "episode", responses)
        initial = harness.observe()
        if mutate:
            mutate(initial)
        runner = ModelRunner(self.credentials, "fixed guide", base_url="https://example.invalid/v1",
                             transport=harness.transport, sleep=lambda duration: None)
        result = runner.run_episode(harness.context, harness.contract, initial,
                                    harness.load_image, execute or harness.execute,
                                    self.root / "logs", limit)
        return harness, result

    def test_no_history_or_privileged_fields_are_sent_to_model(self):
        harness, result = self.run_fixture([response(call()), response()])
        self.assertEqual(result["reason"], "agent_gave_up")
        payload = json.dumps(harness.requests)
        self.assertNotIn("MUST_NOT_REACH_MODEL", payload)
        self.assertNotIn("trial_index", payload)
        self.assertNotIn(str(self.root), payload)
        self.assertNotIn("object_pose", payload)
        second_input = harness.requests[1]["input"]
        self.assertGreater(len(second_input), len(harness.requests[0]["input"]))
        self.assertEqual(len([item for item in second_input if item.get("type") == "function_call"]), 1)
        self.assertEqual(len(harness.loaded), 2)
        self.assertEqual(len(set(harness.loaded)), 2)

    def test_api_retry_never_repeats_a_robot_action(self):
        harness, result = self.run_fixture([
            response(call("one")), APIError("temporary", retryable=True),
            response(call("two")),
        ])
        self.assertEqual(result["reason"], "native_step_limit")
        self.assertEqual([t["op"] for t in harness.tools], ["hold", "hold"])
        self.assertEqual(harness.requests[1], harness.requests[2])
        self.assertEqual(result["model_calls"], 3)

    def test_duplicate_call_id_is_not_dispatched_twice(self):
        harness, result = self.run_fixture([
            response(call("same")), response(call("same")), response(call("new")),
        ])
        self.assertEqual(result["reason"], "native_step_limit")
        self.assertEqual([t["op"] for t in harness.tools], ["hold", "observe", "hold"])
        self.assertEqual(result["validation_errors"], 1)

    def test_parallel_tool_batch_is_rejected_before_any_action(self):
        harness, result = self.run_fixture([
            response(call("first"), call("second")), response(),
        ])
        self.assertEqual([t["op"] for t in harness.tools], ["observe"])
        self.assertEqual(result["validation_errors"], 2)

    def test_lifecycle_file_and_op_override_requests_never_dispatch(self):
        for name, args in (("reset", {}), ("act", {"file": "/elsewhere/trajectory.json"}),
                           ("hold", {"op": "reset", "steps": 1})):
            with self.subTest(name=name, args=args), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                harness = EpisodeHarness(root / "episode", [response(call("bad", name, args)), response()])
                runner = ModelRunner(self.credentials, "guide", base_url="https://example.invalid/v1",
                                     transport=harness.transport, sleep=lambda duration: None)
                result = runner.run_episode(harness.context, harness.contract, harness.observe(),
                                            harness.load_image, harness.execute, root / "logs", 1200)
                self.assertEqual([t["op"] for t in harness.tools], ["observe"])
                self.assertEqual(result["validation_errors"], 1)

    def test_ambiguous_execution_exception_is_not_retried(self):
        executed = []

        def execute(payload):
            executed.append(payload)
            raise TimeoutError("The action may already have executed")

        harness, result = self.run_fixture([response(call()), response(call())], execute=execute)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(len(executed), 1)
        self.assertEqual(len(harness.requests), 1)

    def test_other_episode_image_is_rejected_before_loading(self):
        harness, result = self.run_fixture([], mutate=lambda obs: obs.update(
            contact_sheet=str(self.root / "old-episode" / "obs-0001" / "contact_sheet.jpg")))
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(harness.loaded, [])
        self.assertEqual(harness.requests, [])

    def test_nonofficial_camera_is_rejected_before_loading(self):
        harness, result = self.run_fixture([], mutate=lambda obs: obs["images"].update(
            privileged_camera="/forbidden.png"))
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(harness.loaded, [])

    def test_unexpected_reported_model_never_controls_robot(self):
        model_response = response(call())
        model_response["model"] = "some-other-model"
        harness, result = self.run_fixture([model_response])
        self.assertEqual(result["exception_class"], "UnexpectedReportedModel")
        self.assertEqual(harness.tools, [])

    def test_public_logs_externalize_images_without_credentials(self):
        harness, result = self.run_fixture([response(call("one")), response(call("two"))])
        transcript = (self.root / "logs" / "model_transcript.jsonl").read_text()
        self.assertNotIn("base64,", transcript)
        self.assertNotIn(FAKE_KEY, transcript)
        records = [json.loads(line) for line in transcript.splitlines()]
        requests = [record["request"] for record in records if record["event"] == "model_request"]
        image_ref = requests[0]["input"][-1]["content"][-1]["image_url"]
        self.assertTrue(image_ref["externalized_data_url"])
        saved_image = self.root / "logs" / image_ref["path"]
        self.assertEqual(saved_image.read_bytes(), JPEG)
        self.assertEqual(hashlib.sha256(saved_image.read_bytes()).hexdigest(), image_ref["sha256"])

    def test_retry_budget_has_initial_request_plus_three_retries(self):
        harness, result = self.run_fixture([APIError("down", retryable=True)] * 4)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["reason"], "api_error")
        self.assertEqual(len(harness.requests), 4)
        self.assertEqual(harness.tools, [])

    def test_fresh_history_when_runner_instance_is_reused(self):
        harness_one = EpisodeHarness(self.root / "episode-one", [response(call("old-call")), response()])
        harness_two = EpisodeHarness(self.root / "episode-two", [response()])
        current = [harness_one]
        runner = ModelRunner(self.credentials, "guide", base_url="https://example.invalid/v1",
                             transport=lambda req: current[0].transport(req), sleep=lambda duration: None)
        for index, harness in enumerate((harness_one, harness_two)):
            current[0] = harness
            runner.run_episode(harness.context, harness.contract, harness.observe(), harness.load_image,
                               harness.execute, self.root / f"logs-{index}", 1200)
        self.assertNotIn("old-call", json.dumps(harness_two.requests))
        self.assertEqual(len(harness_two.requests[0]["input"]), 4)

    def test_step_limit_nan_and_dimensions_rejected_before_execution(self):
        contract = {"native_action_dim": 23, "rotvec_action_dim": 22}
        for payload in (
            {"op": "hold", "steps": 31}, {"op": "hold", "steps": True},
            {"op": "hold", "steps": 2}, {"op": "move", "steps": 1, "xyz": [0, 0, float("nan")]},
            {"op": "act", "actions": [[0.0] * 46]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_tool(payload, contract, 1)


class ResponseStreamTests(unittest.TestCase):
    @staticmethod
    def lines(events):
        return [line.encode() for event in events for line in (
            "event: " + event["type"] + "\n", "data: " + json.dumps(event) + "\n", "\n")]

    def test_only_completed_response_returns_and_all_events_are_preserved(self):
        completed = response(call("complete-call"))
        events = [{"type": "response.output_item.added", "item": call("partial-call")},
                  {"type": "response.completed", "response": completed}]
        archived = []
        result = parse_response_stream(self.lines(events), archived.append)
        self.assertEqual(result, completed)
        self.assertEqual(archived, events)

    def test_partial_tool_call_and_eof_raise_retryable_error(self):
        archived = []
        partial = {"type": "response.output_item.added", "item": call("never-dispatch")}
        with self.assertRaises(APIError) as caught:
            parse_response_stream(self.lines([partial]), archived.append)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(archived, [partial])

    def test_explicit_incomplete_is_not_a_model_give_up_or_completed_action(self):
        event = {"type": "response.incomplete", "response": {
            "status": "incomplete", "output": [call("never-dispatch")],
            "incomplete_details": {"reason": "max_output_tokens"}}}
        with self.assertRaises(APIError) as caught:
            parse_response_stream(self.lines([event]))
        self.assertFalse(caught.exception.retryable)

    def test_completed_response_with_no_trailing_blank_line(self):
        completed = response(call())
        raw = b'data: ' + json.dumps({"type": "response.completed", "response": completed}).encode()
        self.assertEqual(parse_response_stream([raw]), completed)

    def test_stream_error_and_done_without_completed_are_rejected(self):
        for lines in (self.lines([{"type": "error", "code": "server_error"}]),
                      [b"data: [DONE]\n", b"\n"]):
            with self.subTest(lines=lines), self.assertRaises(APIError):
                parse_response_stream(lines)


if __name__ == "__main__":
    unittest.main()
