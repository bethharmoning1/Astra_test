#!/usr/bin/env python3
"""Six-task bridge/preview infrastructure validation. NOT an Agent success test.
Reads only returned RGB/robot proprioception; does not manipulate object state.
Refuses any existing default runner. Only cleans up the runner started here.
"""
from pathlib import Path
import http.cookiejar
from io import BytesIO
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
import numpy as np
from PIL import Image
from task_registry import TASKS, catalog

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "agent_bridge" / "bridge.py"
SOCKET = ROOT / "agent_bridge" / "runner.sock"
REPORT = ROOT / "artifacts" / "multitask_report.json"
report = {"kind": "six_single_arm_tasks_infrastructure_only_not_agent_score", "tasks": {}, "checks": {}}
owned_pid = None
if sys.platform != "linux":
    raise SystemExit("Run this integration test locally on Linux with the project DexJoCo virtualenv; source agent_bridge/env_cpu.sh first")


def call(*args, expect_ok=True):
    p = subprocess.run([sys.executable, str(BRIDGE), *args], capture_output=True,
                       text=True, timeout=180)
    try:
        result = json.loads(p.stdout)
    except ValueError:
        if not expect_ok and p.returncode:
            return {"ok": False, "error": p.stderr}
        raise RuntimeError(p.stdout + p.stderr)
    if expect_ok and (p.returncode or not result["ok"]):
        raise RuntimeError(str(result) + p.stderr)
    if not expect_ok and (p.returncode == 0 or result["ok"]):
        raise AssertionError("Invalid request unexpectedly accepted")
    return result["result"] if expect_ok else result


def check(checks, name, condition, **details):
    checks[name] = {"passed": bool(condition), **details}
    if not condition:
        raise AssertionError(name + ": " + str(details))


def state(obs):
    return np.asarray(obs["tcp_pose_wxyz"] + obs["hand_joints"], dtype=float)


def wait_stopped():
    for _ in range(120):
        if not SOCKET.exists():
            return
        time.sleep(.05)
    raise RuntimeError("Runner did not clean up socket")


def stop_owned():
    global owned_pid
    if owned_pid is not None and SOCKET.exists():
        current = call("status")
        if current["pid"] != owned_pid:
            raise RuntimeError("Runner ownership changed; refuse to stop it")
        call("stop")
        wait_stopped()
    owned_pid = None


if SOCKET.exists():
    raise SystemExit("Runner active; refuse to disturb an existing episode")
try:
    c = report["checks"]
    listed = call("tasks")
    check(c, "catalog_without_runner", len(listed["tasks"]) == 11 and not SOCKET.exists())
    supported = [item["task"] for item in listed["tasks"] if item["bridge_supported"] and item["robot_type"] == "single_arm"]
    check(c, "eleven_supported_tasks", sum(t["bridge_supported"] for t in listed["tasks"]) == 11)
    check(c, "six_supported_single_arm", len(supported) == 6)
    for name, info in TASKS.items():
        lines = (ROOT / "configs" / "rand_obj" / (name + ".yaml")).read_text().splitlines()
        prompt = next(line.split(":", 1)[1].strip().strip('"') for line in lines if line.startswith("prompt:"))
        check(c, "official_prompt_" + name, prompt == info["prompt"])
    for name in supported:
        task = report["tasks"][name] = {"checks": {}, "agent_attempted": False}
        checks = task["checks"]
        started = call("start", "--task", name, "--seed", "0", "--label", "multitask-smoke")
        owned_pid = started["pid"]
        check(checks, "start_task_identity", started["task"] == name and started["steps"] == 0)
        episode = Path(started["episode_dir"])
        task["episode_dir"] = str(episode)
        contract = call("contract")
        check(checks, "23d_contract_and_prompt", contract["task"] == name
              and contract["native_action_dim"] == 23 and len(contract["hand_order"]) == 16
              and contract["prompt"] == TASKS[name]["prompt"])
        first = call("observe")
        check(checks, "finite_robot_state_only", state(first).shape == (23,)
              and np.all(np.isfinite(state(first)))
              and not any(k in first for k in ("state", "qpos", "object_pose")))
        shapes = {}
        for camera, image_path in first["images"].items():
            image = np.asarray(Image.open(image_path))
            shapes[camera] = list(image.shape)
            check(checks, "camera_" + camera, image.shape == (640, 640, 3)
                  and image.dtype == np.uint8 and float(image.std()) > 5)
        expected_cameras = next(item["camera_keys"] for item in listed["tasks"] if item["task"] == name)
        check(checks, "official_selected_camera_layout", list(first["images"]) == expected_cameras)
        task["camera_shapes"] = shapes
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        url = started["preview"]["url"]
        base = url.split("/?")[0]
        with client.open(url, timeout=5) as response:
            page = response.read().decode()
        check(checks, "dynamic_read_only_viewer", 'id="goal"' in page and "s.task_description_zh||s.prompt" in page)
        def preview_state():
            with client.open(base + "/status", timeout=5) as response:
                return json.load(response)
        for _ in range(100):
            ps = preview_state()
            if ps.get("frame_sequence", 0) > 0:
                break
            time.sleep(.05)
        check(checks, "preview_task_goal", ps["task"] == name and ps["prompt"] == TASKS[name]["prompt"]
              and ps["task_description_zh"] == TASKS[name]["description_zh"])
        with client.open(base + "/frame.jpg?camera=grid", timeout=5) as response:
            grid = Image.open(BytesIO(response.read()))
            check(checks, "preview_grid", grid.width > 0 and grid.height > 0)
        time.sleep(.3)
        before = call("status")
        check(checks, "paused_clock", before["steps"] == 0 and before["sim_time"] == first["sim_time"]
              and preview_state()["step_count"] == 0)
        reused = call("start")
        same = call("start", "--task", name)
        check(checks, "start_reuses_without_reset", reused["pid"] == same["pid"] == owned_pid
              and same["episode_dir"] == str(episode))
        other = "fold_glasses" if name == "click_mouse" else "click_mouse"
        call("start", "--task", other, expect_ok=False)
        call("reset", "--task", other, "--label", "wrong-task", expect_ok=False)
        current = call("status")
        check(checks, "reject_mismatched_task_no_reset", current["task"] == name
              and current["episode_dir"] == str(episode) and current["steps"] == 0)
        held = call("hold", "--steps", "6", "--note", "infra_hold")
        check(checks, "hold_pose", held["step_count"] == 6
              and float(np.linalg.norm(state(held)[:3] - state(first)[:3])) < .02)
        moved = call("move", "--xyz", ".003", "0", "0", "--delta", "--steps", "8", "--note", "infra_small_motion")
        check(checks, "small_motion_executes", moved["step_count"] == 14
              and abs(moved["last_target_23"][0] - held["tcp_pose_wxyz"][0] - .003) < 1e-10
              and float(np.linalg.norm(state(moved)[:3] - state(held)[:3])) > 1e-5)
        from scipy.spatial.transform import Rotation
        target = np.asarray(moved["last_target_23"])
        rotvec = Rotation.from_quat(target[3:7][[1,2,3,0]]).as_rotvec()
        action22 = np.concatenate((target[:3], rotvec, target[7:])).tolist()
        action_file = episode / "infra_actions22.json"
        action_file.write_text(json.dumps([action22, action22]))
        acted = call("act", "--file", str(action_file), "--note", "infra_rotvec")
        check(checks, "22d_action_conversion", acted["step_count"] == 16)
        final = call("hold", "--steps", "4")
        check(checks, "upstream_success_flag_bool", isinstance(final["succeed"], bool) and final["step_count"] == 20)
        task["smoke_succeed"] = final["succeed"]
        call("move", "--xyz", "99", "0", "1", "--steps", "1", expect_ok=False)
        check(checks, "invalid_action_no_advance", call("status")["steps"] == 20)
        updated = preview_state()
        check(checks, "preview_success_propagation", updated["task"] == name
              and updated["step_count"] == 20 and updated["succeed"] == final["succeed"])
        summary = call("finish", "--reason", "infrastructure_smoke_only")
        persisted = json.loads((episode / "summary.json").read_text())
        manifest = json.loads((episode / "manifest.json").read_text())
        check(checks, "summary_and_manifest", summary == persisted and summary["task"] == name
              and summary["steps"] == 20 and not summary["formal_benchmark"]
              and manifest["task"] == name and manifest["prompt"] == TASKS[name]["prompt"]
              and not manifest["privileged_state_exposed"])
        call("hold", "--steps", "1", expect_ok=False)
        again = call("reset", "--task", name, "--seed", "0", "--label", "multitask-reset")
        task["reset_episode_dir"] = again["episode_dir"]
        check(checks, "same_task_reset_reproducible", again["task"] == name and again["step_count"] == 0
              and np.allclose(state(first), state(again), atol=1e-8, rtol=0))
        call("hold", "--steps", "2")
        call("finish", "--reason", "infrastructure_reset_only")
        stop_owned()
        check(checks, "socket_cleanup", not SOCKET.exists())
        task["all_passed"] = all(item["passed"] for item in checks.values())
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"task": name, "checks": len(checks), "all_passed": task["all_passed"]}), flush=True)
    for name in [item["task"] for item in catalog()["tasks"] if not item["bridge_supported"]]:
        call("start", "--task", name, expect_ok=False)
        check(c, "reject_unsupported_" + name, not SOCKET.exists())
    report["all_passed"] = (all(item["passed"] for item in c.values())
                            and len(report["tasks"]) == 6
                            and all(item["all_passed"] for item in report["tasks"].values()))
except Exception as exc:
    report["error"] = repr(exc)
    report["all_passed"] = False
    raise
finally:
    stop_owned()
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(REPORT), "all_passed": report.get("all_passed", False)}), flush=True)
