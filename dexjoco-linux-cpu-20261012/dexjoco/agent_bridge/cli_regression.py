#!/usr/bin/env python3
"""Small lifecycle/CLI regression, separate from Codex performance episodes."""
from pathlib import Path
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "agent_bridge" / "bridge.py"
SOCKET = ROOT / "agent_bridge" / "runner.sock"
report = {"kind": "bridge_cli_regression_not_agent_evaluation", "checks": {}}

def call(*args, should_succeed=True):
    p = subprocess.run([sys.executable, str(BRIDGE), *args], capture_output=True,
                       text=True, timeout=180)
    result = json.loads(p.stdout)
    if should_succeed and (p.returncode or not result["ok"]):
        raise RuntimeError(str(result) + p.stderr)
    return p.returncode, result

if SOCKET.exists():
    raise SystemExit("A runner is active; refuse to disturb it")
try:
    _, start = call("start", "--seed", "42", "--label", "bridge-regression")
    report["checks"]["start"] = {"passed": start["result"]["steps"] == 0}
    _, contract = call("contract")
    report["checks"]["contract"] = {"passed": contract["result"]["task"] == "click_mouse"}
    _, before = call("status")
    _, observed = call("observe")
    time.sleep(0.3)
    _, after = call("status")
    report["checks"]["pause_between_requests"] = {
        "passed": before["result"]["steps"] == after["result"]["steps"] == 0}
    _, moved = call("move", "--xyz", "0.003", "0", "0", "--delta", "--steps", "20",
                    "--note", "cli_small_motion")
    _, held = call("hold", "--steps", "10")
    report["checks"]["move_hold"] = {"passed": moved["result"]["step_count"] == 20
                                     and held["result"]["step_count"] == 30}
    code, invalid = call("move", "--xyz", "99", "0", "1", "--steps", "1", should_succeed=False)
    _, status = call("status")
    report["checks"]["invalid_action_without_advance"] = {
        "passed": code != 0 and not invalid["ok"] and status["result"]["steps"] == 30}
    _, finished = call("finish", "--reason", "regression_complete")
    code, post = call("hold", "--steps", "1", should_succeed=False)
    report["checks"]["reject_actions_after_finish"] = {"passed": code != 0 and not post["ok"]}
    _, stopped = call("stop")
    report["checks"]["finish_stop_idempotent"] = {"passed": finished == stopped}
    for _ in range(30):
        if not SOCKET.exists():
            break
        time.sleep(0.1)
    report["checks"]["socket_cleanup"] = {"passed": not SOCKET.exists()}
    report["all_passed"] = all(check["passed"] for check in report["checks"].values())
finally:
    if SOCKET.exists():
        call("stop")
path = ROOT / "artifacts" / "bridge_regression_report.json"
path.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
if not report["all_passed"]:
    raise SystemExit(1)
