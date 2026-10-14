"""Synthetic unit fixtures only; none of these are real evaluation evidence."""
from __future__ import annotations

import base64
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evidence import TASKS, archive_attempt, update_batch, verify_inventory


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def write_jsonl(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.batch = self.root / "batch"
        self.config = {"batch_id": "synthetic-unit-test", "tasks": list(TASKS), "trials_per_task": 5}

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, task="click_mouse", trial=1, *, success=True, status="completed", attempt=1):
        attempt_id = f"trial{trial:02d}-attempt{attempt:02d}"
        source = self.root / "sources" / task / attempt_id
        episode, external = source / "episode", source / "external"
        episode.mkdir(parents=True)
        external.mkdir()
        dim = 46 if task.startswith("bimanual_") else 23
        cameras = (["ego", "wrist_left", "wrist_right"] if dim == 46 else
                   ["ego_right", "wrist"] if task == "click_mouse" else ["front", "wrist"])
        label = f"synthetic-{task}-{attempt_id}"
        identity = {"task": task, "seed": 0, "label": label}
        manifest = {**identity, "native_action_dim": dim, "camera_keys": cameras}
        write_json(episode / "manifest.json", manifest)
        observations = []
        for number, steps in ((1, 0), (2, 2)):
            folder = episode / f"obs-{number:04d}"
            folder.mkdir()
            for camera in cameras:
                (folder / (camera + ".png")).write_bytes(PNG)
            (folder / "contact_sheet.jpg").write_bytes(b"synthetic-contact-sheet-bytes")
            observation = {"task": task, "seed": 0, "episode_dir": str(episode), "step_count": steps,
                           "action_calls": int(steps > 0), "robot_state": [0] * dim,
                           "camera_keys": cameras, "images": {camera: str(folder / (camera + ".png")) for camera in cameras},
                           "contact_sheet": str(folder / "contact_sheet.jpg"), "succeed": success and steps > 0,
                           "terminated": steps > 0, "truncated": False}
            write_json(folder / "observation.json", observation)
            observations.append({"event": "observation", "path": str(folder),
                                 "proprioception": [0] * 23, "succeed": observation["succeed"]})
        request = {"op": "hold", "steps": 2}
        events = [{"event": "reset", "seed": 0}, observations[0],
                  {"event": "agent_request", "request": request},
                  {"event": "step", "step": 1, "succeed": False, "terminated": False, "truncated": False, "robot_state": [0] * dim},
                  {"event": "step", "step": 2, "succeed": success, "terminated": True, "truncated": False, "robot_state": [0] * dim},
                  observations[1]]
        write_jsonl(episode / "events.jsonl", events)
        summary = {**identity, "steps": 2, "action_calls": 1, "succeed": success,
                   "terminated": True, "truncated": False, "reason": "synthetic-fixture", "native_action_dim": dim,
                   "episode_dir": str(episode)}
        write_json(episode / "summary.json", summary)
        commands = [{"request": {"op": "hold", "steps": 31}, "response": {"ok": False, "error": "steps exceeds 30"},
                     "returncode": 1, "timestamp": "2026-10-14T00:00:00Z"},
                    {"request": request, "response": {"ok": True, "result": {"step_count": 2}},
                     "returncode": 0, "timestamp": "2026-10-14T00:00:01Z"},
                    {"request": {"op": "finish"}, "response": {"ok": True, "result": summary},
                     "returncode": 0, "timestamp": "2026-10-14T00:00:02Z"}]
        write_jsonl(external / "command_log.jsonl", commands)
        (external / "runner-log.txt").write_text("", encoding="utf-8")
        write_json(external / "dispatch.json", {"task": task, "fixed_prompt": "synthetic test"})
        write_json(external / "initial_state_verification.json", {"passed": True, "method": "synthetic unit test"})
        write_jsonl(external / "model_transcript.jsonl", [{"event": "model_request", "request": {"model": "synthetic"}},
                                                       {"event": "model_response", "response": {"output": "synthetic"}}])
        metadata = {**identity, "batch_id": self.config["batch_id"], "trial_index": trial,
                    "attempt_id": attempt_id, "scored": True, "status": status, "phase": "model_execution",
                    "isolation": {"verified": True, "method": "synthetic unit test"},
                    "command_count": len(commands), "command_error_count": 1}
        destination = self.batch / "episodes" / task / attempt_id
        return {"episode_dir": episode, "attempt_dir": external, "destination": destination, "metadata": metadata}

    def load(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def csv_rows(self, filename):
        with (self.batch / filename).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def model_image_fixture(self, *, attempt=1):
        args = self.fixture(attempt=attempt)
        content = b"\xff\xd8\xff\xe0synthetic-model-image\xff\xd9"
        digest = hashlib.sha256(content).hexdigest()
        (args["episode_dir"] / "obs-0001/contact_sheet.jpg").write_bytes(content)
        relative = f"model_images/{digest}.jpg"
        path = args["attempt_dir"] / relative
        path.parent.mkdir()
        path.write_bytes(content)
        reference = {"externalized_data_url": True, "path": relative, "mime_type": "image/jpeg",
                     "sha256": digest, "source_observation": "obs-0001",
                     "source_archive_path": "obs-0001/contact_sheet.jpg"}
        return args, reference

    def save_model_image_reference(self, args, reference):
        write_jsonl(args["attempt_dir"] / "model_transcript.jsonl", [
            {"event": "model_request", "request": {"input": [{"role": "user", "content": [
                {"type": "input_image", "image_url": reference}]}]}},
            {"event": "model_response", "response": {"output": []}},
        ])

    def test_full_archive_redacts_tokens_preserves_files_and_original_paths(self):
        args = self.fixture()
        token = "super-secret-preview-0123"
        extra = {"preview": {"token": token, "url": "http://127.0.0.1:8766/?token=" + token},
                 "action": [1, 2], "step": 7}
        write_json(args["attempt_dir"] / "preview.json", extra)
        (args["attempt_dir"] / "runner-log.txt").write_text("token in URL http://localhost/?token=" + token + "\nTraceback: preserved\n")
        (args["attempt_dir"] / ".env").write_text("API_KEY=must-not-be-archived\n")
        (args["attempt_dir"] / "runner.pid").write_text("123")
        (args["attempt_dir"] / "credentials.txt").write_text("must-not-be-archived")
        raw = b'{"unknown": [1, 2], "leave_spacing":    true}\n'
        (args["episode_dir"] / "extra.json").write_bytes(raw)
        record = archive_attempt(**args)
        self.assertTrue(record["evidence_complete"])
        destination = args["destination"]
        self.assertEqual((destination / "extra.json").read_bytes(), raw)
        self.assertTrue(verify_inventory(destination)["passed"])
        for path in destination.rglob("*"):
            if path.is_file():
                self.assertNotIn(token.encode(), path.read_bytes(), str(path))
                self.assertNotIn(b"must-not-be-archived", path.read_bytes(), str(path))
        preview = self.load(destination / "preview.json")
        self.assertEqual(preview["preview"]["token"], "<redacted>")
        self.assertEqual(preview["step"], 7)
        self.assertEqual(preview["action"], [1, 2])
        self.assertIn("Traceback: preserved", (destination / "runner-log.txt").read_text())
        mapping = self.load(destination / "source_path_mapping.json")
        self.assertTrue(all(item["exists"] for item in mapping["referenced_paths"]))
        observation = self.load(destination / "obs-0001/observation.json")
        self.assertEqual(observation["episode_dir"], str(args["episode_dir"]))
        self.assertEqual(self.load(destination / "command_errors.json")["count"], 1)
        inventory = self.load(destination / "evidence_inventory.json")
        self.assertNotIn("evidence_inventory.json", [item["path"] for item in inventory["files"]])
        self.assertIn("audit.json", [item["path"] for item in inventory["files"]])
        self.assertFalse((destination / "missing_evidence.json").exists())
        self.assertIn(token, (args["attempt_dir"] / "preview.json").read_text())

    def test_preserves_collision_and_refuses_overwrite(self):
        args = self.fixture()
        (args["episode_dir"] / "extra.txt").write_text("episode version")
        (args["attempt_dir"] / "extra.txt").write_text("attempt version")
        (args["attempt_dir"] / "audit.json").write_text('{"raw":true}')
        archive_attempt(**args)
        self.assertEqual((args["destination"] / "extra.txt").read_text(), "episode version")
        self.assertEqual((args["destination"] / "attempt-files/extra.txt").read_text(), "attempt version")
        self.assertTrue((args["destination"] / "source-files/attempt/audit.json").is_file())
        with self.assertRaises(FileExistsError):
            archive_attempt(**args)

    def test_missing_image_and_unknown_schema_are_explicit(self):
        args = self.fixture()
        (args["episode_dir"] / "obs-0002/wrist.png").unlink()
        write_json(args["episode_dir"] / "summary.json", {"future_schema": {"outcome": "unknown"}})
        record = archive_attempt(**args)
        self.assertFalse(record["evidence_complete"])
        self.assertIsNone(record["succeed"])
        missing = self.load(args["destination"] / "missing_evidence.json")["missing"]
        self.assertTrue(any("wrist.png" in item["reason"] for item in missing))
        self.assertTrue(any(item["item"] == "summary.json:succeed" for item in missing))
        self.assertEqual(self.load(args["destination"] / "summary.json"), {"future_schema": {"outcome": "unknown"}})

    def test_detects_step_action_reset_and_terminal_inconsistency(self):
        args = self.fixture()
        path = args["episode_dir"] / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[4]["step"] = 3
        events[4]["terminated"] = False
        events.insert(0, {"event": "reset", "seed": 0})
        events.insert(3, {"event": "agent_request", "request": {"op": "hold", "steps": 1}})
        write_jsonl(path, events)
        record = archive_attempt(**args)
        self.assertEqual(record["audit_status"], "failed")
        failed = {item["name"] for item in self.load(args["destination"] / "audit.json")["checks"] if item["status"] == "failed"}
        self.assertTrue({"consecutive_step_numbers", "summary_action_calls", "one_reset_per_episode", "summary_terminated"} <= failed)

    def test_success_is_ever_true_and_dual_state_must_be_46(self):
        args = self.fixture(task="bimanual_assembly")
        path = args["episode_dir"] / "events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[3]["succeed"], events[4]["succeed"] = True, False
        write_jsonl(path, events)
        record = archive_attempt(**args)
        self.assertTrue(record["evidence_complete"])
        self.assertTrue(record["succeed"])
        second = self.fixture(task="bimanual_assembly", trial=2)
        observation_path = second["episode_dir"] / "obs-0002/observation.json"
        observation = self.load(observation_path)
        observation["robot_state"] = [0] * 23
        write_json(observation_path, observation)
        self.assertFalse(archive_attempt(**second)["evidence_complete"])

    def test_startup_interruption_keeps_evidence_and_does_not_fabricate_summary(self):
        external = self.root / "startup"
        external.mkdir()
        write_jsonl(external / "command_log.jsonl", [{"request": {"op": "serve"}, "returncode": 1,
            "timestamp": "2026-10-14T00:00:00Z", "response": {"ok": False, "error": "port occupied"}}])
        (external / "runner-log.txt").write_text("Traceback: Address already in use")
        write_json(external / "dispatch.json", {"task": "click_mouse"})
        destination = self.batch / "episodes/click_mouse/startup"
        metadata = {"batch_id": self.config["batch_id"], "task": "click_mouse", "trial_index": 1,
                    "attempt_id": "startup", "seed": 0, "label": "startup", "status": "interrupted", "scored": True,
                    "phase": "startup", "exception_class": "OSError", "reason": "port occupied"}
        record = archive_attempt(episode_dir=None, attempt_dir=external, destination=destination, metadata=metadata)
        self.assertFalse(record["scored"])
        self.assertIsNone(record["succeed"])
        self.assertFalse((destination / "summary.json").exists())
        self.assertFalse((destination / "events.jsonl").exists())
        self.assertTrue((destination / "missing_evidence.json").exists())
        update_batch(self.batch, self.config, [record])
        rows = self.csv_rows("task_results.csv")
        self.assertEqual(rows[0]["failures"], "0")
        self.assertEqual(rows[0]["interrupted_attempts"], "1")
        self.assertEqual(rows[0]["completed_scored_episodes"], "0")

    def test_jsonl_corruption_keeps_error_line_pointer_and_timeout_null_code(self):
        args = self.fixture()
        path = args["attempt_dir"] / "command_log.jsonl"
        timeout = {"request": {"op": "observe"}, "returncode": None, "exception": "TimeoutExpired",
                   "response": {"ok": False, "error": "timeout"}, "timestamp": "2026-10-14T00:00:03Z"}
        path.write_text("invalid json\n" + path.read_text() + json.dumps(timeout) + "\n")
        args["metadata"].update(command_count=4, command_error_count=2)
        archive_attempt(**args)
        errors = self.load(args["destination"] / "command_errors.json")
        self.assertEqual([row["command_log_line"] for row in errors["errors"]], [2, 5])
        self.assertIsNone(errors["errors"][1]["returncode"])
        self.assertIn("invalid json", (args["destination"] / "command_log.jsonl").read_text())
        missing = self.load(args["destination"] / "missing_evidence.json")["missing"]
        self.assertFalse(any(item["item"].endswith(":returncode") for item in missing))

    def test_checksum_detects_tampering_and_unlisted_files(self):
        args = self.fixture()
        archive_attempt(**args)
        (args["destination"] / "runner-log.txt").write_text("tampering")
        (args["destination"] / "unlisted.txt").write_text("new evidence")
        verification = verify_inventory(args["destination"])
        self.assertFalse(verification["passed"])
        self.assertIn("Size or SHA-256 mismatch: runner-log.txt", verification["errors"])
        self.assertIn("Unlisted file: unlisted.txt", verification["errors"])

    def test_partial_batch_lists_all_tasks_without_formal_rates(self):
        records = [archive_attempt(**self.fixture(trial=trial, success=trial == 1)) for trial in range(1, 5)]
        records.append(archive_attempt(**self.fixture(trial=5, status="interrupted")))
        (self.batch / "PLAN.md").write_text("synthetic plan")
        write_json(self.batch / "attempts.json", records)
        update_batch(self.batch, self.config, records)
        rows = self.csv_rows("task_results.csv")
        self.assertEqual(len(rows), 11)
        self.assertTrue(all(row["task_success_rate"] == "" for row in rows))
        self.assertEqual(rows[0]["completed_scored_episodes"], "4")
        self.assertEqual(rows[0]["successes"], "1")
        self.assertEqual(rows[0]["failures"], "3")
        self.assertEqual(rows[0]["interrupted_attempts"], "1")
        self.assertNotIn("总体任务成功率：", (self.batch / "report.md").read_text())
        root_files = {item["path"] for item in self.load(self.batch / "evidence_inventory.json")["files"]}
        self.assertTrue({"PLAN.md", "attempts.json", "batch_config.json", "task_results.csv"} <= root_files)
        self.assertIn(records[0]["evidence_path"] + "/evidence_inventory.json", root_files)
        self.assertTrue(verify_inventory(self.batch)["passed"])

    def test_exactly_55_scored_episodes_publish_zero_percent_without_dropping_tasks(self):
        records = []
        for task in TASKS:
            for trial in range(1, 6):
                records.append(archive_attempt(**self.fixture(task=task, trial=trial, success=False)))
        update_batch(self.batch, self.config, records)
        rows = self.csv_rows("task_results.csv")
        self.assertEqual(len(rows), 11)
        self.assertTrue(all(row["task_success_rate"] == "0%" for row in rows))
        self.assertIn("总体任务成功率：0/55 = 0.00%", (self.batch / "report.md").read_text())
        self.assertEqual(len(self.csv_rows("episode_results.csv")), 55)

    def test_duplicate_completed_trial_does_not_select_best_five(self):
        records = [archive_attempt(**self.fixture(trial=trial, success=False)) for trial in range(1, 6)]
        records.append(archive_attempt(**self.fixture(trial=1, success=True, attempt=2)))
        update_batch(self.batch, self.config, records)
        rows = self.csv_rows("task_results.csv")
        self.assertEqual(rows[0]["task_success_rate"], "")
        self.assertIn("duplicate", rows[0]["reason"])
        changed = {**self.config, "model": "changed"}
        with self.assertRaisesRegex(ValueError, "frozen"):
            update_batch(self.batch, changed, records)

    def test_escaped_credential_redaction_keeps_valid_json(self):
        args = self.fixture()
        write_json(args["attempt_dir"] / "extra.json", {"password": 'private-escaped"value', "safe": [3, 4]})
        result = archive_attempt(**args)
        self.assertTrue(result["evidence_complete"])
        self.assertEqual(self.load(args["destination"] / "extra.json"), {"password": "<redacted>", "safe": [3, 4]})

    def test_result_table_does_not_claim_complete_audit_when_record_is_altered(self):
        args = self.fixture()
        result = archive_attempt(**args)
        result["succeed"] = False
        update_batch(self.batch, self.config, [result])
        rows = self.csv_rows("episode_results.csv")
        self.assertEqual(rows[0]["evidence_complete"], "false")
        self.assertEqual(rows[0]["audit_status"], "failed")
        self.assertIn("differs from archived attempt.json", (self.batch / "report.md").read_text())

    def test_actual_recorder_and_sse_model_transcript_integrate_with_archive(self):
        """Mock only CLI/network boundaries; retain actual producer record formats."""
        import subprocess
        from model_runner import ModelRunner, parse_response_stream
        from run_batch import CommandRecorder

        args = self.fixture()
        external = args["attempt_dir"]
        (external / "command_log.jsonl").unlink()
        (external / "model_transcript.jsonl").unlink()
        for path in args["episode_dir"].glob("obs-*/contact_sheet.jpg"):
            path.write_bytes(b"\xff\xd8\xff\xe0synthetic-jpeg\xff\xd9")
        initial = self.load(args["episode_dir"] / "obs-0001/observation.json")
        final = self.load(args["episode_dir"] / "obs-0002/observation.json")
        summary = self.load(args["episode_dir"] / "summary.json")
        contract = {"task": "click_mouse", "native_action_dim": 23, "rotvec_action_dim": 22,
                    "camera_keys": ["ego_right", "wrist"]}
        recorder = CommandRecorder(external, external / "runner.sock")

        def command(op, result=None, *, request=None, rc=0):
            payload = {"ok": True, "result": result}
            process = subprocess.CompletedProcess(["synthetic-bridge"], rc,
                json.dumps(payload) if rc == 0 else "", "FileNotFoundError: no socket" if rc else "")
            with patch("run_batch.subprocess.run", return_value=process):
                return recorder.call(op, request=request)

        command("status", rc=1)
        recorder.append({"started_at": "synthetic", "ended_at": "synthetic", "request": {"op": "serve"},
                         "returncode": 0, "response": {"ok": True, "pid": 123, "phase": "process_spawned"}})
        command("status", {"pid": 123, "steps": 0})
        command("contract", contract)
        command("observe", initial)
        credentials = self.root / "synthetic-credential.md"
        credentials.write_text("sk-unit-test-credential-never-valid")

        def transport(request):
            response = {"id": "synthetic-response", "model": "gpt-6-astra", "status": "completed", "output": [{
                "type": "function_call", "call_id": "synthetic-call", "name": "hold",
                "arguments": json.dumps({"steps": 2, "note": "synthetic"})}]}
            events = [{"type": "response.created", "response": {"id": "synthetic-response"}},
                      {"type": "response.completed", "response": response}]
            lines = []
            for event in events:
                lines.extend([("data: " + json.dumps(event)).encode() + b"\n", b"\n"])
            return parse_response_stream(lines, runner._stream_event_sink)

        runner = ModelRunner(credentials, "synthetic fixed guide", base_url="https://example.invalid/v1",
                             transport=transport, sleep=lambda duration: None)

        def execute(request):
            self.assertEqual(request["op"], "hold")
            return command("hold", final, request=request)

        result = runner.run_episode({"task": "click_mouse", "seed": 0}, contract, initial,
            lambda path: {"mime_type": "image/jpeg", "data": Path(path).read_bytes(),
                          "archive_path": Path(path).relative_to(args["episode_dir"]).as_posix()},
            execute, external, 1200)
        self.assertEqual(result["status"], "completed", (external / "model_transcript.jsonl").read_text())
        command("status", {"pid": 123, "steps": 2})
        command("finish", summary)
        command("stop", summary)
        args["metadata"].update(command_count=recorder.count, command_error_count=recorder.error_count)
        archived = archive_attempt(**args)
        self.assertTrue(archived["evidence_complete"])
        self.assertEqual(archived["audit_counts"]["command_error_count"], 1)
        transcript = [json.loads(line) for line in (args["destination"] / "model_transcript.jsonl").read_text().splitlines()]
        self.assertEqual(sum(row["event"] == "model_stream_event" for row in transcript), 2)
        self.assertTrue(list((args["destination"] / "model_images").glob("*.jpg")))
        self.assertEqual(archived["audit_counts"]["unique_model_image_references"], 1)

    def test_missing_externalized_model_image_is_reported(self):
        args, reference = self.model_image_fixture()
        self.save_model_image_reference(args, reference)
        (args["attempt_dir"] / reference["path"]).unlink()
        archived = archive_attempt(**args)
        self.assertFalse(archived["evidence_complete"])
        gaps = self.load(args["destination"] / "missing_evidence.json")["missing"]
        self.assertTrue(any(reference["path"] in row["reason"] for row in gaps))

    def test_model_image_digest_and_mime_are_checked(self):
        for attempt, mutation in enumerate(("image_bytes", "declared_hash", "declared_mime", "image_extension"), 1):
            with self.subTest(mutation=mutation):
                args, reference = self.model_image_fixture(attempt=attempt)
                if mutation == "image_bytes":
                    (args["attempt_dir"] / reference["path"]).write_bytes(b"\xff\xd8\xff\xe0different-jpeg\xff\xd9")
                elif mutation == "declared_hash":
                    reference["sha256"] = "0" * 64
                elif mutation == "declared_mime":
                    reference["mime_type"] = "image/png"
                else:
                    old = args["attempt_dir"] / reference["path"]
                    old.rename(old.with_suffix(".png"))
                    reference["path"] = str(Path(reference["path"]).with_suffix(".png"))
                self.save_model_image_reference(args, reference)
                self.assertFalse(archive_attempt(**args)["evidence_complete"])

    def test_model_image_paths_and_original_observation_are_checked(self):
        mutations = (
            ("path", "../../outside.jpg"), ("path", "/absolute/image.jpg"),
            ("source_archive_path", "../outside.jpg"),
            ("source_archive_path", "obs-0002/contact_sheet.jpg"),
            ("source_observation", "obs-9999"),
        )
        for attempt, (key, value) in enumerate(mutations, 1):
            with self.subTest(key=key, value=value):
                args, reference = self.model_image_fixture(attempt=attempt)
                reference[key] = value
                self.save_model_image_reference(args, reference)
                self.assertFalse(archive_attempt(**args)["evidence_complete"])


if __name__ == "__main__":
    unittest.main()
