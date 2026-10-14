#!/usr/bin/env python3
"""Run an existing infrastructure test with only a preview-port override."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = ROOT / "agent_bridge"
BRIDGE = BRIDGE_DIR / "bridge.py"
OUTPUT = Path(__file__).resolve().parent
PORT = 18766
REPORTS = {
    "cli_regression": "bridge_regression_report.json",
    "preview_regression": "preview_integration_report.json",
    "multitask_test": "multitask_report.json",
    "bimanual_test": "bimanual_report.json",
}
name = sys.argv[1]
if name not in REPORTS:
    raise SystemExit("Unsupported infrastructure test")
run_dir = OUTPUT / name
run_dir.mkdir()
script = BRIDGE_DIR / (name + ".py")
token_fingerprints = {}


def scrub(value):
    if not isinstance(value, str):
        return value
    def replacement(match):
        value = match.group(1)
        digest = hashlib.sha256(value.encode()).hexdigest()
        token_fingerprints[digest] = "preview-token-redacted-" + digest[:12]
        return "token=<" + token_fingerprints[digest] + ">"
    return re.sub(r"token=([^\s\"&<>]+)", replacement, value)


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def event(record):
    with (run_dir / "commands.jsonl").open("a") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def normalize(command):
    if not isinstance(command, (list, tuple)):
        return command, False
    command = [str(part) for part in command]
    is_bridge = len(command) > 2 and Path(command[1]).resolve() == BRIDGE
    patched = (is_bridge and "start" in command[2:]
               and "--preview-port" not in command)
    return command + (["--preview-port", str(PORT)] if patched else []), patched


original_run = subprocess.run
original_popen = subprocess.Popen


def recorded_run(command, *args, **kwargs):
    actual, patched = normalize(command)
    t0 = time.monotonic()
    record = {"kind": "subprocess.run", "started_utc": datetime.now(timezone.utc).isoformat(),
              "original_command": command, "command": actual,
              "preview_port_injected": patched}
    try:
        completed = original_run(actual, *args, **kwargs)
        record.update(returncode=completed.returncode,
                      stdout=scrub(completed.stdout), stderr=scrub(completed.stderr))
        return completed
    except BaseException as exc:
        record["exception"] = repr(exc)
        raise
    finally:
        record["elapsed_seconds"] = time.monotonic() - t0
        event(record)


def port_popen(command, *args, **kwargs):
    actual, patched = normalize(command)
    process = original_popen(actual, *args, **kwargs)
    event({"kind": "subprocess.Popen", "started_utc": datetime.now(timezone.utc).isoformat(),
           "original_command": command, "command": actual,
           "preview_port_injected": patched, "pid": process.pid})
    return process


files = [BRIDGE, script, BRIDGE_DIR / "preview.py", BRIDGE_DIR / "robot_actions.py",
         BRIDGE_DIR / "task_registry.py"]
source_before = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in files}
if (BRIDGE_DIR / "runner.sock").exists():
    raise SystemExit("Existing runner socket; refusing to run")
with socket.socket() as check:
    check.bind(("127.0.0.1", PORT))

runner_log = BRIDGE_DIR / "runner.log"
log_offset = runner_log.stat().st_size if runner_log.exists() else 0
started = datetime.now(timezone.utc).isoformat()
t0 = time.monotonic()
subprocess.run = recorded_run
subprocess.Popen = port_popen
os.chdir(BRIDGE_DIR)
sys.path.insert(0, str(BRIDGE_DIR))
sys.argv = [str(script)]
returncode = 0
error = None
try:
    runpy.run_path(str(script), run_name="__main__")
except SystemExit as exc:
    returncode = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
    if returncode:
        error = repr(exc)
        traceback.print_exc()
except BaseException as exc:
    returncode = 1
    error = repr(exc)
    traceback.print_exc()
finally:
    subprocess.run = original_run
    subprocess.Popen = original_popen
    report_path = ROOT / "artifacts" / REPORTS[name]
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    if report is not None:
        save_json(run_dir / REPORTS[name], report)
    if runner_log.exists():
        with runner_log.open("rb") as handle:
            handle.seek(log_offset)
            (run_dir / "runner.log").write_text(scrub(handle.read().decode(errors="replace")))
    source_after = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in files}
    result = {"kind": "infrastructure_regression_not_agent_evaluation", "test": name,
              "started_utc": started, "elapsed_seconds": time.monotonic() - t0,
              "returncode": returncode, "error": error,
              "preview_port": PORT, "socket": str(BRIDGE_DIR / "runner.sock"),
              "socket_absent_after_test": not (BRIDGE_DIR / "runner.sock").exists(),
              "all_passed": report.get("all_passed") if report else None,
              "environment": {key: os.environ.get(key) for key in
                  ("MUJOCO_GL", "PYOPENGL_PLATFORM", "LIBGL_ALWAYS_SOFTWARE",
                   "JAX_PLATFORMS", "CUDA_VISIBLE_DEVICES")},
              "source_hash_before": source_before, "source_hash_after": source_after,
              "source_unchanged": source_before == source_after,
              "redaction": {"kind": "preview URL token", "sha256_to_placeholder": token_fingerprints}}
    save_json(run_dir / "execution.json", result)
    print(json.dumps({"test": name, "returncode": returncode, "all_passed": result["all_passed"],
                      "elapsed_seconds": result["elapsed_seconds"],
                      "socket_absent": result["socket_absent_after_test"]}), flush=True)
raise SystemExit(returncode)
