#!/usr/bin/env python3
"""External scheduler for isolated visual episodes and per-attempt publication."""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "dexjoco-linux-cpu-20261012/dexjoco"
ENTRY = PROJECT / "agent_bridge/run_cpu.sh"
TASK_LIMITS = {
    "click_mouse": 1200, "fold_glasses": 1200, "hammer_nail": 1000,
    "pick_bucket": 1200, "pinch_tongs": 1000, "water_plant": 1000,
    "bimanual_assembly": 1500, "bimanual_hanoi": 1500,
    "bimanual_microwave_cook": 1100, "bimanual_photograph": 1000,
    "bimanual_unlock_ipad": 1200,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def git(*args):
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()


class InfrastructureError(RuntimeError):
    pass


class CommandRecorder:
    def __init__(self, directory, socket_path):
        self.directory = directory
        self.path = directory / "command_log.jsonl"
        self.path.touch()
        self.socket_path = socket_path
        self.count = 0
        self.error_count = 0

    def append(self, row):
        self.count += 1
        row["sequence"] = self.count
        if row.get("returncode", 0) or row.get("response", {}).get("ok") is False or row.get("exception"):
            self.error_count += 1
        with self.path.open("a") as file:
            file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    def call(self, op, *arguments, request=None, timeout=210):
        command = ["bash", str(ENTRY), "--socket", str(self.socket_path), op, *map(str, arguments)]
        started = now()
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            def retained_output(value):
                return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
            self.append({"started_at": started, "ended_at": now(), "command": command,
                         "request": request or {"op": op}, "returncode": None,
                         "exception": "TimeoutExpired", "timeout_seconds": timeout,
                         "stdout": retained_output(exc.stdout), "stderr": retained_output(exc.stderr),
                         "response": {"ok": False, "error": "CLI timeout; action must not be replayed"}})
            raise InfrastructureError("CLI timeout; action state is uncertain and is not replayed") from exc
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {"ok": False, "error": "CLI returned no valid JSON"}
        self.append({"started_at": started, "ended_at": now(), "command": command,
                     "request": request or {"op": op}, "returncode": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr, "response": payload})
        return payload


def require_ok(payload):
    if not payload.get("ok"):
        raise InfrastructureError(payload.get("error", "bridge request failed"))
    return payload["result"]


def compare_initial(observation, baseline_directory, output):
    """Compare only permitted RGB, proprioception and public camera metadata."""
    import numpy as np
    from PIL import Image
    values = np.asarray(observation["robot_state"], dtype=float)
    cameras = observation["camera_keys"]
    config_hash = observation["camera_config_sha256"]
    first = not baseline_directory.exists()
    if first:
        baseline_directory.mkdir(parents=True)
        write_json(baseline_directory / "state.json", {
            "robot_state": values.tolist(), "camera_keys": cameras,
            "camera_config_sha256": config_hash,
        })
        for camera in cameras:
            shutil.copy2(observation["images"][camera], baseline_directory / f"{camera}.png")
    reference = json.loads((baseline_directory / "state.json").read_text())
    expected = np.asarray(reference["robot_state"], dtype=float)
    state_ok = values.shape == expected.shape and bool(np.allclose(values, expected, atol=1e-8, rtol=0))
    camera_checks = {}
    for camera in cameras:
        current = np.asarray(Image.open(observation["images"][camera]))
        previous = np.asarray(Image.open(baseline_directory / f"{camera}.png"))
        camera_checks[camera] = {
            "pixels_equal": bool(np.array_equal(current, previous)),
            "initial_png_sha256": digest(observation["images"][camera]),
            "reference_png_sha256": digest(baseline_directory / f"{camera}.png"),
        }
    result = {
        "method": "exact decoded RGB; proprioception atol=1e-8 rtol=0; configuration SHA-256",
        "is_reference": first, "state_absolute_tolerance": 1e-8,
        "state_max_absolute_error": float(np.max(np.abs(values - expected))) if values.shape == expected.shape else None,
        "robot_state_equal": state_ok,
        "camera_configuration_equal": cameras == reference["camera_keys"] and config_hash == reference["camera_config_sha256"],
        "cameras": camera_checks,
        "reference_visible_to_model": False,
    }
    result["passed"] = state_ok and result["camera_configuration_equal"] and all(x["pixels_equal"] for x in camera_checks.values())
    write_json(output, result)
    return result


class RestrictedEpisode:
    """The model receives this callable, never a filesystem or shell tool."""
    def __init__(self, recorder, episode, contract, observation, limit):
        self.recorder = recorder
        self.episode = episode.resolve()
        self.contract = contract
        self.latest = observation
        self.limit = limit
        self.action_file_count = 0

    def image_loader(self, supplied_path):
        path = Path(supplied_path).resolve()
        allowed = {Path(v).resolve() for v in self.latest["images"].values()}
        allowed.add(Path(self.latest["contact_sheet"]).resolve())
        if path not in allowed or not path.is_relative_to(self.episode):
            raise PermissionError("Only the current observation's official images are available")
        return {"mime_type": "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
                "data": path.read_bytes(), "archive_path": str(path.relative_to(self.episode)),
                "sha256": digest(path)}

    def _reject(self, message, request):
        payload = {"ok": False, "error": message}
        self.recorder.append({"started_at": now(), "ended_at": now(), "request": request,
                              "returncode": 1, "response": payload, "stage": "restricted_tool_validation"})
        return payload

    def execute_tool(self, request):
        if not isinstance(request, dict):
            return self._reject("Tool request must be an object", {"invalid_request": request})
        op = request.get("op")
        allowed = {
            "observe": {"op"}, "hold": {"op", "steps", "note"},
            "move": {"op", "steps", "note", "xyz", "quat", "rotvec", "hand", "arm", "delta"},
            "act": {"op", "actions", "note"},
        }
        if op not in allowed or set(request) - allowed[op]:
            return self._reject("Only current-episode observe/move/hold/act with documented fields are allowed", request)
        if op != "observe" and (self.latest["succeed"] or self.latest["terminated"] or self.latest["truncated"]):
            return self._reject("Episode has ended; no more actions", request)
        previous_steps = self.latest["step_count"]
        previous_actions = self.latest.get("action_calls", 0)
        if op == "observe":
            payload = self.recorder.call(op, request=request)
        else:
            remaining = self.limit - self.latest["step_count"]
            if op == "act":
                actions = request.get("actions")
                if not isinstance(actions, list) or not actions or not all(isinstance(row, list) for row in actions):
                    return self._reject("act requires a 2D action array with 1..30 rows", request)
                steps = len(actions)
                dimensions = {self.contract["native_action_dim"], self.contract["rotvec_action_dim"]}
                if any(len(row) not in dimensions or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in row) for row in actions):
                    return self._reject("Invalid action dimensions or non-finite values", request)
            else:
                steps = request.get("steps", 30)
            if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= min(30, remaining):
                return self._reject(f"Action steps must be in 1..{min(30, remaining)}", request)
            arguments = []
            if op == "act":
                self.action_file_count += 1
                path = self.recorder.directory / f"action-{self.action_file_count:04d}.json"
                write_json(path, actions)
                arguments += ["--file", str(path)]
            else:
                arguments += ["--steps", str(steps)]
            if op == "move":
                if request.get("quat") is not None and request.get("rotvec") is not None:
                    return self._reject("Use quat or rotvec, not both", request)
                for key, length in (("xyz", 3), ("quat", 4), ("rotvec", 3), ("hand", 16)):
                    if request.get(key) is not None:
                        vector = request[key]
                        if not isinstance(vector, list) or len(vector) != length or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in vector):
                            return self._reject(f"{key} requires {length} finite numbers", request)
                        arguments += ["--" + key, *map(str, vector)]
                if request.get("arm") is not None:
                    if request["arm"] not in ("right", "left"):
                        return self._reject("arm must be right or left", request)
                    arguments += ["--arm", request["arm"]]
                if request.get("delta"):
                    arguments.append("--delta")
            if request.get("note"):
                arguments += ["--note", str(request["note"])[:2000]]
            payload = self.recorder.call(op, *arguments, request=request)
        if payload.get("ok"):
            observed = payload["result"]
            if Path(observed["episode_dir"]).resolve() != self.episode:
                raise InfrastructureError("Bridge changed episode identity unexpectedly")
            self.latest = observed
        else:
            if op == "observe" or "returned no valid JSON" in payload.get("error", ""):
                raise InfrastructureError(payload.get("error", "Observation failed"))
            # The bridge uses the same ok:false envelope for pre-action validation
            # and failures after execution began. Inspect this episode before
            # permitting correction; an uncertain/partially executed action is
            # never treated as a harmless rejection or replayed.
            verification = self.recorder.call("observe", request={"op": "observe", "purpose": "verify_failed_action_did_not_start"})
            observed = require_ok(verification)
            if Path(observed["episode_dir"]).resolve() != self.episode:
                raise InfrastructureError("Bridge changed episode identity during failed-action check")
            self.latest = observed
            if observed["step_count"] != previous_steps or observed.get("action_calls", 0) != previous_actions:
                raise InfrastructureError("Bridge reported an error after action execution began; action is not replayed")
        return payload


def tracked_runtime_hashes():
    paths = [REPO / "CODEX_AGENT.md", REPO / "README.md"]
    paths += sorted((REPO / "tools/evaluation").glob("*.py"))
    paths += sorted((PROJECT / "agent_bridge").glob("*.py"))
    paths += sorted((PROJECT / "agent_bridge").glob("*.sh"))
    paths += sorted((PROJECT / "configs/rand_obj").glob("*.yaml"))
    paths += [PROJECT / "agent_bridge/requirements.lock.txt"]
    return {str(p.relative_to(REPO)): digest(p) for p in paths}


def check_frozen(config):
    encoded = json.dumps({k: v for k, v in config.items() if k != "frozen_config_sha256"},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != config["frozen_config_sha256"]:
        raise InfrastructureError("Frozen batch configuration checksum mismatch")
    for relative, expected in config["frozen_files"].items():
        if digest(REPO / relative) != expected:
            raise InfrastructureError(f"Frozen configuration changed: {relative}; start a new batch")


def publish(batch_dir, message):
    if git("branch", "--show-current") != "test":
        raise InfrastructureError("Result branch must remain test")
    relative = str(batch_dir.relative_to(REPO))
    for path in batch_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            if re.search(rb"\bsk-[A-Za-z0-9_-]{16,}", path.read_bytes()):
                raise InfrastructureError(f"Possible credential in archive: {path.relative_to(REPO)}")
    subprocess.run(["git", "-C", str(REPO), "add", "--", relative], check=True)
    subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--check"], check=True)
    tracked = set(git("ls-files", "-z", "--", relative).split("\0"))
    for path in batch_dir.rglob("*"):
        if path.is_file():
            if path.stat().st_size >= 99 * 1024**2:
                raise InfrastructureError(f"Evidence file too large for normal Git: {path.relative_to(REPO)}")
            if str(path.relative_to(REPO)) not in tracked:
                raise InfrastructureError(f"Evidence not tracked: {path.relative_to(REPO)}")
    if subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--quiet"]).returncode:
        subprocess.run(["git", "-C", str(REPO), "commit", "-m", message], check=True)
    local = git("rev-parse", "HEAD")
    for retry in range(3):
        result = subprocess.run(["git", "-C", str(REPO), "push", "origin", "test"], text=True, capture_output=True)
        if result.returncode == 0:
            remote = git("ls-remote", "--heads", "origin", "refs/heads/test").split()[0]
            if remote != local:
                raise InfrastructureError("Remote test does not match the published local commit")
            return local
        print(json.dumps({"event": "push_retry", "attempt": retry + 1, "stderr": result.stderr}), flush=True)
        time.sleep(2 ** retry)
    raise InfrastructureError("Per-attempt push failed; local evidence committed, batch stopped")


def run_attempt(config, task, trial, attempt_number, credentials, base_url, baseline_directory):
    from model_runner import ModelRunner
    attempt_id = f"trial{trial:02d}-attempt{attempt_number:02d}"
    label = f"eval-{config['batch_id']}-{task}-t{trial:02d}-a{attempt_number:02d}"
    private = PROJECT / "artifacts/evaluation-state" / config["batch_id"] / task / attempt_id
    private.mkdir(parents=True, exist_ok=False, mode=0o700)
    metadata = {
        "batch_id": config["batch_id"], "task": task, "trial_index": trial,
        "attempt_id": attempt_id, "seed": 0, "label": label, "scored": False,
        "status": "interrupted", "reason": "startup_incomplete", "phase": "startup",
        "replacement_of": f"trial{trial:02d}-attempt{attempt_number-1:02d}" if attempt_number > 1 else "",
        "source_commit": config["source_commit"], "expected_reset_count": 1,
        "isolation": {"method": "new API history; no filesystem/shell tools; current-episode callbacks only", "verified": True},
        "model": config["model"], "frozen_config_sha256": config["frozen_config_sha256"],
        "platform_limitations": ["Hidden model reasoning and independent backend model version are unavailable."],
    }
    write_json(private / "dispatch.json", {**metadata, "initial_prompt": config["initial_prompt"],
        "guide_sha256": config["guide_sha256"], "observation_strategy": config["observation_strategy"],
        "frozen_files": config["frozen_files"], "started_at": now(),
        "bridge_metadata_note": "Original bridge manifest/summary has legacy static model_api_used=false and exploratory labels. Actual API identity and protocol are recorded here; originals are preserved."})
    socket_directory = Path(tempfile.mkdtemp(prefix="astra-eval-"))
    socket_path = socket_directory / "runner.sock"
    recorder = CommandRecorder(private, socket_path)
    episode = None
    process = None
    restricted = None
    runner_output = (private / "runner-log.txt").open("w")
    try:
        recorder.call("status")
        command = ["bash", str(ENTRY), "--socket", str(socket_path), "serve", "--task", task,
                   "--seed", "0", "--label", label, "--no-preview"]
        launched = now()
        process = subprocess.Popen(command, stdout=runner_output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        recorder.append({"started_at": launched, "ended_at": now(), "request": {"op": "serve", "task": task, "seed": 0, "label": label, "no_preview": True},
                         "command": command, "returncode": 0, "response": {"ok": True, "pid": process.pid, "phase": "process_spawned"}})
        deadline = time.monotonic() + 180
        while not socket_path.exists():
            if process.poll() is not None:
                raise InfrastructureError(f"Owned runner exited during startup with code {process.returncode}")
            if time.monotonic() > deadline:
                raise InfrastructureError("Owned runner did not become ready within 180 seconds")
            time.sleep(0.25)
        status = require_ok(recorder.call("status"))
        if status["pid"] != process.pid or status["task"] != task or status["seed"] != 0 or status["label"] != label or status["steps"] != 0:
            raise InfrastructureError("New runner identity or initial step count mismatch")
        episode = Path(status["episode_dir"]).resolve()
        contract = require_ok(recorder.call("contract"))
        observation = require_ok(recorder.call("observe"))
        write_json(private / "contract.json", contract)
        verification = compare_initial(observation, baseline_directory, private / "initial_state_verification.json")
        metadata["initial_state_verification"] = verification
        if not verification["passed"]:
            raise InfrastructureError("Initial official RGB/proprioception/configuration differs from frozen task reference")
        metadata["phase"] = "model_execution"
        restricted = RestrictedEpisode(recorder, episode, contract, observation, TASK_LIMITS[task])
        runner = ModelRunner(credentials_path=credentials, guide_text=config["guide_text"], base_url=base_url)
        model_result = runner.run_episode(
            context={"task": task, "seed": 0, "batch_id": config["batch_id"], "trial_index": trial, "attempt_id": attempt_id},
            contract=contract, initial_observation=observation,
            image_loader=restricted.image_loader, execute_tool=restricted.execute_tool,
            output_dir=private, step_limit=TASK_LIMITS[task],
        )
        write_json(private / "model_result.json", model_result)
        reason = model_result.get("reason", "agent_gave_up")
        interrupted = model_result.get("status") == "interrupted"
        latest = restricted.latest
        if latest["succeed"]:
            reason, interrupted = "official_success", False
        elif latest["terminated"] or latest["truncated"]:
            reason, interrupted = "official_failure" if latest["step_count"] < TASK_LIMITS[task] else "native_step_limit", False
        elif latest["step_count"] >= TASK_LIMITS[task]:
            reason, interrupted = "native_step_limit", False
        metadata.update(status="interrupted" if interrupted else "completed", scored=not interrupted,
                        reason=reason, exception_class=model_result.get("exception_class", ""))
    except Exception as exc:
        # These exceptions describe runner/IO machinery; API errors are sanitized by ModelRunner.
        metadata.update(status="interrupted", scored=False, reason="infrastructure_error", exception_class=type(exc).__name__)
        write_json(private / "execution_error.json", {"phase": metadata["phase"], "type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
    finally:
        cleanup = {"own_pid": process.pid if process else None, "runner_exited": process is None}
        if process is not None and process.poll() is None:
            try:
                if socket_path.exists():
                    status = require_ok(recorder.call("status"))
                    if status["pid"] != process.pid or status["label"] != label:
                        raise InfrastructureError("Refusing to clean runner with different ownership")
                    if episode is None:
                        episode = Path(status["episode_dir"]).resolve()
                    require_ok(recorder.call("finish", "--reason", metadata["reason"], request={"op": "finish", "reason": metadata["reason"]}))
                    require_ok(recorder.call("stop"))
                process.wait(timeout=20)
            except Exception as exc:
                cleanup["error"] = str(exc)
                # Only this subprocess is terminated; never a name/global process match.
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
            cleanup["runner_exited"] = process.poll() is not None
        if process is not None:
            cleanup["returncode"] = process.poll()
            cleanup["runner_exited"] = process.poll() is not None
        cleanup["socket_absent"] = not socket_path.exists()
        if cleanup["socket_absent"]:
            socket_directory.rmdir()
        runner_output.close()
        write_json(private / "cleanup.json", cleanup)
        metadata.update(command_count=recorder.count, command_error_count=recorder.error_count, cleanup=cleanup)
        write_json(private / "attempt_metadata.json", metadata)
    return episode, private, metadata


def initialize(batch_dir, batch_id, base_url):
    from model_runner import ModelRunner
    source_commit = git("rev-parse", "HEAD")
    frozen = tracked_runtime_hashes()
    config = {
        "batch_id": batch_id, "created_at": now(), "source_commit": source_commit,
        "upstream_commit": (PROJECT / "UPSTREAM_COMMIT.txt").read_text().strip(),
        "guide_sha256": digest(REPO / "CODEX_AGENT.md"), "guide_text": (REPO / "CODEX_AGENT.md").read_text(),
        "initial_prompt": "请你读取CODEX_AGENT.md 这个教程 进行任务的测试",
        "model": {"requested_model": "gpt-6-astra", "reasoning_effort": "medium", "backend_version_independently_verified": False},
        "api_base_url": base_url, "model_policy": ModelRunner.policy_manifest(),
        "api_socket_timeout_seconds": 180,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "packages": {name: importlib.metadata.version(name) for name in ("mujoco", "gymnasium", "numpy", "Pillow")}},
        "tasks": list(TASK_LIMITS), "trials_per_task": 5, "task_step_limits": TASK_LIMITS,
        "seed": 0, "max_steps_per_request": 30, "preview_enabled": False,
        "max_parallel_tasks": 3, "same_task_trials_sequential": True,
        "observation_strategy": "One official contact-sheet JPEG per observation, full current-episode history retained; raw official PNGs archived.",
        "initial_comparison": {"rgb": "decoded pixels exactly equal", "proprio_atol": 1e-8, "proprio_rtol": 0, "camera_config_sha256": "equal"},
        "interruption_rule": "Only demonstrable infrastructure faults may be replaced; all attempts archived; maximum three attempts per scored trial before marking blocked.",
        "branch": "test", "frozen_files": frozen,
        "platform_limitations": ["Hidden model reasoning and backend version cannot be independently exported/verified."],
    }
    config["frozen_config_sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    write_json(batch_dir / "batch_config.json", config)
    write_json(batch_dir / "attempts.json", [])
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", default="20261014-astra-medium")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--initialize-only", action="store_true")
    args = parser.parse_args()
    from evidence import archive_attempt, update_batch
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.batch_id):
        raise ValueError("batch-id must be a short directory name without path separators")
    lock_path = Path(git("rev-parse", "--absolute-git-dir")) / "evaluation-scheduler.lock"
    scheduler_lock = lock_path.open("a")
    try:
        fcntl.flock(scheduler_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise InfrastructureError("Another evaluation scheduler owns this repository") from None
    if git("branch", "--show-current") != "test":
        raise InfrastructureError("Evaluation must run on the user-selected test branch")
    batch_dir = REPO / "reports/evaluations" / args.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    config_path = batch_dir / "batch_config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else initialize(batch_dir, args.batch_id, args.base_url)
    if config["api_base_url"] != args.base_url:
        raise InfrastructureError("API provider changed; a new batch is required")
    check_frozen(config)
    attempts = json.loads((batch_dir / "attempts.json").read_text())
    update_batch(batch_dir, config, attempts)
    if args.initialize_only:
        return
    default_socket = PROJECT / "agent_bridge/runner.sock"
    if default_socket.exists():
        raise InfrastructureError("A default runner already exists; ownership must be resolved before evaluation")
    baseline_root = PROJECT / "artifacts/evaluation-state" / args.batch_id / "initial_references"
    halted = None
    active = {}
    with ThreadPoolExecutor(max_workers=config["max_parallel_tasks"]) as executor:
        while True:
            active_tasks = {item[0] for item in active.values()}
            if not halted:
                for task in TASK_LIMITS:
                    if len(active) >= config["max_parallel_tasks"]:
                        break
                    if task in active_tasks:
                        continue
                    for trial in range(1, 6):
                        matching = [row for row in attempts if row["task"] == task and row["trial_index"] == trial]
                        if any(row.get("scored") for row in matching):
                            continue
                        attempt_number = len(matching) + 1
                        if attempt_number > 3:
                            halted = "Three recorded infrastructure interruptions for one scored trial; resolve before resuming"
                            break
                        check_frozen(config)
                        print(json.dumps({"event": "attempt_start", "task": task, "trial": trial, "attempt": attempt_number, "time": now()}), flush=True)
                        future = executor.submit(run_attempt, config, task, trial, attempt_number,
                                                 args.credentials, args.base_url, baseline_root / task)
                        active[future] = (task, trial, attempt_number)
                        break
                    if halted:
                        break
            if not active:
                break
            completed, _ = wait(active, timeout=30, return_when=FIRST_COMPLETED)
            if not completed:
                print(json.dumps({"event": "heartbeat", "active": list(active.values()),
                                  "scored": sum(bool(r.get("scored")) for r in attempts), "time": now()}), flush=True)
            for future in completed:
                task, trial, attempt_number = active.pop(future)
                try:
                    episode, private, metadata = future.result()
                    destination = batch_dir / "episodes" / task / metadata["attempt_id"]
                    record = archive_attempt(episode_dir=episode, attempt_dir=private, destination=destination, metadata=metadata)
                    attempts.append(record)
                    write_json(batch_dir / "attempts.json", attempts)
                    update_batch(batch_dir, config, attempts)
                    commit = publish(batch_dir, f"Record {task} trial {trial} attempt {attempt_number}")
                    print(json.dumps({"event": "attempt_published", "record": record, "commit": commit}, ensure_ascii=False), flush=True)
                    if metadata["scored"] and (not record.get("evidence_complete") or record.get("audit_status") not in ("passed", "pass")):
                        halted = "Scored attempt published with incomplete evidence; resolve audit findings before continuing"
                    if not metadata["scored"] and (metadata.get("exception_class") or metadata["reason"] in ("api_error", "infrastructure_error")):
                        halted = "Interrupted attempt archived and pushed; resolve fault before resuming"
                except Exception as exc:
                    halted = f"Archive/publication failure: {type(exc).__name__}: {exc}"
                    print(json.dumps({"event": "archive_error", "task": task, "trial": trial, "error": halted}), flush=True)
                # On a fault, finish and archive already-running independent episodes,
                # but do not start more. No unfinished evidence is silently discarded.
    if halted:
        write_json(batch_dir / "batch_interruption.json", {"time": now(), "reason": halted})
        update_batch(batch_dir, config, attempts)
        publish(batch_dir, "Record evaluation interruption and remaining task progress")
        raise InfrastructureError(halted)
    update_batch(batch_dir, config, attempts)
    commit = publish(batch_dir, "Finalize all-task evaluation report")
    print(json.dumps({"event": "batch_finished", "scored": sum(bool(r.get("scored")) for r in attempts), "planned": 55, "commit": commit}), flush=True)


if __name__ == "__main__":
    main()
