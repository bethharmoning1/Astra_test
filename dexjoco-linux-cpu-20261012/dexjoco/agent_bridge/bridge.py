#!/usr/bin/env python3
"""Local Unix-socket DexJoCo runner; optional loopback read-only preview.
The simulator advances only during explicit action requests.
"""
from __future__ import annotations
import argparse
import json
from importlib import import_module
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOCKET = ROOT / "agent_bridge" / "runner.sock"
from task_registry import DEFAULT_TASK, TASKS, CAMERA_METADATA_FIELDS, catalog, require_supported

from robot_actions import RobotActions, POSITION_BOUNDS, HAND_NAMES, MAX_STEPS_PER_REQUEST


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False)


def request(message, socket_path=DEFAULT_SOCKET):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(180)
        sock.connect(str(socket_path))
        sock.sendall((dumps(message) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            part = sock.recv(65536)
            if not part:
                raise RuntimeError("Runner closed connection without a result")
            data += part
        return json.loads(data)


class Runner:
    def __init__(self, seed, label, preview=None, task=DEFAULT_TASK):
        self.task = task
        self.task_info = require_supported(task)
        self.prompt = self.task_info["prompt"]
        self.cameras = tuple(self.task_info["camera_keys"])
        self.camera_config = {key: self.task_info[key] for key in CAMERA_METADATA_FIELDS}
        if preview is not None and tuple(preview.cameras) != self.cameras:
            raise ValueError("Agent and read-only preview must use the same official camera mapping")
        self.dual = self.task_info["robot_type"] == "dual_arm"
        self.action_dim = self.task_info["native_action_dim"]
        self.preview = preview
        self.phase = "resetting"
        self.active_operation = ""
        self.last_operation = ""
        self.action_note = ""
        os.environ.setdefault("MUJOCO_GL", "egl")
        os.environ.setdefault("MUJOCO_EGL_DEVICE_ID", "1")
        import numpy as np
        TaskConfig = import_module(f"dexjoco.tasks.{task}.config").TaskConfig
        if TaskConfig.proprio_keys[:2] != ["tcp_pose", "gripper_pose"]:
            raise ValueError("Task proprioception layout is not compatible with the non-privileged robot adapter")
        self.np = np
        self.env = TaskConfig().get_environment(
            policy_mode=True, render_mode="rgb_array", randomize=False, seed=seed)
        self.raw = self.env.unwrapped
        self.episode = None
        try:
            if (self.env.action_space.shape != (self.action_dim,)
                    or self.env.proprio_space["tcp_pose"].shape != ((14,) if self.dual else (7,))):
                raise ValueError("Task action/TCP space does not match the pinned robot adapter")
            self.robot = RobotActions.from_environment(self.raw, self.dual)
            self.limits = self.robot.limits if self.dual else self.robot.limits["single"]
            self.reset(seed, label)
        except BaseException:
            self.env.close()
            raise

    def reset(self, seed, label):
        if not label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in label):
            raise ValueError("label must be a safe alphanumeric name")
        import random
        if self.episode is not None:
            self.finish("reset")
        self.phase = "resetting"
        self.publish_preview(force=True)
        # Upstream reset(seed=...) does not reseed its global RNGs. Explicitly seed
        # both here; do not change task reset, dynamics or success conditions.
        random.seed(seed)
        self.np.random.seed(seed)
        self.obs, self.info = self.env.reset(seed=seed)
        # Some upstream tasks declare gripper_pose as shape (1,) although the
        # actual returned Allegro state has 16 joints. Check the real data below;
        # do not rewrite upstream observation spaces or expose the object tail.
        if any(key not in self.obs for key in self.cameras) or self.obs["state"].size < self.action_dim:
            raise ValueError("Task does not expose the expected task RGB cameras and robot state")
        self.target = self.robot.validate(self.robot.measured(self.obs["state"]))
        self.step_count = self.action_calls = self.obs_index = 0
        self.success = self.terminated = self.truncated = False
        self.rewards = []
        self.seed = seed
        self.label = label
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.episode = ROOT / "artifacts" / f"{self.task}-{label}-seed{seed}-{stamp}-{time.time_ns()%1000000:06}"
        self.episode.mkdir(parents=True)
        self.t0 = time.monotonic()
        self.finished = False
        self.phase = "paused"
        self.active_operation = self.last_operation = self.action_note = ""
        (self.episode / "manifest.json").write_text(dumps({
            "task": self.task, "seed": seed, "label": label,
            "prompt": self.prompt, "prompt_source": self.task_info["prompt_source"],
            "robot_type": self.task_info["robot_type"], "native_action_dim": self.action_dim, "randomize_visuals": False,
            "observation_fields": ["rgb", f"state[:{self.action_dim}]"], "camera_keys": self.cameras,
            **self.camera_config,
            "privileged_state_exposed": False,
            "source_commit": (ROOT / "UPSTREAM_COMMIT.txt").read_text().strip(),
            "model_api_used": False, "agent_identity": "current_Codex_session_model_not_independently_verified", "clock": "paused_between_action_calls",
            "purpose": "exploratory_agent_test_not_formal_benchmark",
        }) + "\n")
        self.log({"event": "reset", "seed": seed})
        self.publish_preview(force=True)
        return self.observe()

    def publish_preview(self, force=False):
        if self.preview is None or self.episode is None:
            return
        # Do not send unselected RGB or privileged state to the preview boundary.
        self.preview.publish({key: self.obs[key] for key in self.cameras}, {
            "task": self.task, "prompt": self.prompt,
            "task_description_zh": self.task_info["description_zh"], "camera_keys": self.cameras,
            **self.camera_config,
            "seed": self.seed, "label": self.label,
            "step_count": self.step_count, "action_calls": self.action_calls,
            "sim_time": float(self.raw.data.time), "succeed": bool(self.success),
            "terminated": bool(self.terminated), "truncated": bool(self.truncated),
            "phase": self.phase, "active_operation": self.active_operation,
            "last_operation": self.last_operation, "action_note": self.action_note,
            **self.robot.fields(self.obs["state"]),
        }, force=force)

    def log(self, event):
        event = {"wall_time": time.time(), "sim_time": float(self.raw.data.time), **event}
        with (self.episode / "events.jsonl").open("a") as out:
            out.write(dumps(event) + "\n")

    def contract(self):
        return {
            "task": self.task, "prompt": self.prompt,
            "prompt_source": self.task_info["prompt_source"], "robot_type": self.task_info["robot_type"],
            "native_action_dim": self.action_dim, "rotvec_action_dim": self.robot.rotvec_dim,
            "native_action": ("absolute world [right_pose7, left_pose7, right_hand16, left_hand16]; pose7=xyz3+wxyz4"
                              if self.dual else "absolute world [xyz(3), quaternion_wxyz(4), hand(16)]"),
            "rotvec_action": ("[right_xyz3, right_rotvec3, right_hand16, left_xyz3, left_rotvec3, left_hand16]"
                              if self.dual else "absolute world [xyz(3), rotation_vector_rad(3), hand(16)]"),
            "hand_order": self.robot.hand_names if self.dual else self.robot.hand_names["single"],
            "hand_joint_limits_rad": ({arm: lim.tolist() for arm, lim in self.robot.limits.items()}
                                      if self.dual else self.limits.tolist()),
            "position_bounds_m": POSITION_BOUNDS, "position_bounds_source": "bridge validation, not an upstream universal limit",
            "move_arm_selection": "required: right|left; unselected arm retains last target; act controls both synchronously" if self.dual else "omit --arm",
            "control_dt_seconds": float(self.raw.control_dt),
            "max_steps_per_request": MAX_STEPS_PER_REQUEST, "episode_step_limit": None,
            "recommended_agent_step_budget": 1200,
            "observations": f"{len(self.cameras)} official per-task selected RGB views and {self.action_dim}D robot proprioception only",
            "camera_keys": self.cameras, **self.camera_config,
            "success": "upstream info.succeed; termination is NOT sufficient for success",
            "clock": "simulator paused while agent reasons; hold explicitly to advance",
        }

    def observe(self):
        from PIL import Image, ImageDraw
        self.obs_index += 1
        folder = self.episode / f"obs-{self.obs_index:04d}"
        folder.mkdir()
        images = {}
        frames = []
        for key in self.cameras:
            frame = self.obs[key]
            Image.fromarray(frame).save(folder / (key + ".png"))
            images[key] = str(folder / (key + ".png"))
            tile = Image.fromarray(frame).resize((480, 480))
            canvas = Image.new("RGB", (480, 505), (24, 24, 24))
            canvas.paste(tile, (0, 25))
            ImageDraw.Draw(canvas).text((8, 6), key, fill="white")
            frames.append(canvas)
        board = Image.new("RGB", (960, 505 * ((len(frames) + 1) // 2)))
        for i, frame in enumerate(frames):
            board.paste(frame, ((i % 2) * 480, (i // 2) * 505))
        board.save(folder / "contact_sheet.jpg", quality=92)
        result = {
            "task": self.task, "prompt": self.prompt, "seed": self.seed,
            "episode_dir": str(self.episode), "step_count": self.step_count,
            "action_calls": self.action_calls, "sim_time": float(self.raw.data.time),
            **self.robot.fields(self.obs["state"]),
            f"last_target_{self.action_dim}": self.target.tolist(), "succeed": bool(self.success),
            "terminated": bool(self.terminated), "truncated": bool(self.truncated),
            "camera_keys": self.cameras, **self.camera_config,
            "images": images, "contact_sheet": str(folder / "contact_sheet.jpg"),
        }
        (folder / "observation.json").write_text(dumps(result) + "\n")
        self.log({"event": "observation", "path": str(folder),
                  "proprioception": self.obs["state"][:23].tolist(),
                  "succeed": bool(self.success)})
        return result

    def validate(self, action):
        return self.robot.validate(action)

    def step(self, a):
        self.obs, reward, term, trunc, self.info = self.env.step(a)
        self.step_count += 1
        self.success = self.success or bool(self.info.get("succeed", False))
        self.terminated, self.truncated = bool(term), bool(trunc)
        self.rewards.append(float(reward))
        self.target = a.copy()
        self.log({"event": "step", "step": self.step_count, f"action_{self.action_dim}": a.tolist(),
                  "reward": float(reward), "succeed": bool(self.info.get("succeed", False)),
                  "terminated": bool(term), "truncated": bool(trunc),
                  **self.robot.fields(self.obs["state"])})
        self.publish_preview()

    def execute(self, msg):
        if self.finished or self.terminated or self.truncated:
            raise ValueError("Episode is finished; reset before sending new actions")
        steps = int(msg.get("steps", 30))
        if not 1 <= steps <= MAX_STEPS_PER_REQUEST:
            raise ValueError(f"steps must be in [1,{MAX_STEPS_PER_REQUEST}]")
        op = msg["op"]
        note = str(msg.get("note", ""))[:2000]
        sequence = []
        np = self.np
        if op == "hold":
            sequence = [self.validate(self.target.copy())] * steps
        elif op == "move":
            sequence = self.robot.move_sequence(self.obs["state"], self.target, msg, steps)
        elif op == "act":
            sequence = self.robot.native_sequence(msg["actions"])
        else:
            raise ValueError("Unknown action operation")
        self.action_calls += 1
        self.log({"event": "agent_request", "request": msg, "note": note})
        self.phase = "executing"
        self.active_operation = self.last_operation = op
        self.action_note = note
        self.publish_preview(force=True)
        try:
            for action in sequence:
                self.step(action)
                if self.terminated or self.truncated:
                    break
        finally:
            self.phase = "finished" if self.terminated or self.truncated else "paused"
            self.active_operation = ""
            self.publish_preview(force=True)
        return self.observe()

    def finish(self, reason="agent_finish"):
        if self.finished:
            return json.loads((self.episode / "summary.json").read_text())
        summary = {"task": self.task, "prompt": self.prompt, "seed": self.seed, "label": self.label,
                   "succeed": bool(self.success), "terminated": bool(self.terminated),
                   "truncated": bool(self.truncated), "steps": self.step_count,
                   "action_calls": self.action_calls, "reason": reason,
                   "simulation_seconds": float(self.raw.data.time),
                   "elapsed_wall_seconds": time.monotonic() - self.t0,
                   "reward_sum": sum(self.rewards), "episode_dir": str(self.episode),
                   "robot_type": self.task_info["robot_type"], "native_action_dim": self.action_dim,
                   "camera_keys": self.cameras, **self.camera_config,
                    "formal_benchmark": False, "model_api_used": False}
        (self.episode / "summary.json").write_text(dumps(summary)+"\n")
        self.finished = True
        self.phase = "finished"
        self.active_operation = ""
        self.publish_preview(force=True)
        return summary


def serve(args):
    require_supported(args.task or DEFAULT_TASK)
    path = Path(args.socket)
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        raise RuntimeError("Socket exists; check status/stop the runner instead of replacing it")
    from preview import Preview
    preview = None if args.no_preview else Preview(
        args.preview_port, args.preview_fps, cameras=require_supported(args.task or DEFAULT_TASK)["camera_keys"])
    try:
        runner = Runner(args.seed, args.label, preview=preview, task=args.task or DEFAULT_TASK)
    except BaseException:
        if preview is not None:
            preview.close()
        raise
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(4)
        print(dumps({"ready": True, "pid": os.getpid(), "socket": str(path)}), flush=True)
        running = True
        try:
            while running:
                conn, _ = server.accept()
                with conn:
                    try:
                        data = b""
                        while not data.endswith(b"\n"):
                            part = conn.recv(65536)
                            if not part:
                                break
                            data += part
                            if len(data) > 1024*1024:
                                raise ValueError("Request exceeds 1 MiB")
                        msg = json.loads(data)
                        op = msg["op"]
                        if op == "status":
                            result = {"pid": os.getpid(), "task": runner.task, "prompt": runner.prompt,
                                      "seed": runner.seed, "label": runner.label,
                                      "finished": runner.finished, "terminated": runner.terminated,
                                      "truncated": runner.truncated, "sim_time": float(runner.raw.data.time),
                                      "episode_dir": str(runner.episode),
                                      "steps": runner.step_count, "succeed": runner.success,
                                      "robot_type": runner.task_info["robot_type"], "native_action_dim": runner.action_dim, "camera_keys": runner.cameras,
                                      **runner.camera_config,
                                       "preview": preview.descriptor() if preview else {"enabled": False}}
                        elif op == "contract":
                            result = runner.contract()
                        elif op == "reset":
                            if msg.get("task") is not None and msg["task"] != runner.task:
                                raise ValueError(f"Active task is {runner.task}; reset cannot switch tasks. Confirm ownership, stop, then start --task {msg['task']}")
                            label = str(msg.get("label", "agent"))
                            if not label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in label):
                                raise ValueError("label must be a safe alphanumeric name")
                            result = runner.reset(int(msg.get("seed", 0)), label)
                        elif op == "observe":
                            result = runner.observe()
                        elif op in ("hold", "move", "act"):
                            result = runner.execute(msg)
                        elif op == "finish":
                            result = runner.finish(msg.get("reason", "agent_finish"))
                        elif op == "stop":
                            result = runner.finish("runner_stop")
                            running = False
                        else:
                            raise ValueError("Unsupported operation")
                        payload = {"ok": True, "result": result}
                    except Exception as exc:
                        traceback.print_exc()
                        payload = {"ok": False, "error": str(exc)}
                    conn.sendall((dumps(payload)+"\n").encode())
        finally:
            try:
                if preview is not None:
                    preview.close()
            finally:
                runner.env.close()
                path.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--socket", default=str(DEFAULT_SOCKET))
    sub = p.add_subparsers(dest="command", required=True)
    for op in ("start", "serve", "reset"):
        s = sub.add_parser(op)
        s.add_argument("--seed", type=int, default=0)
        s.add_argument("--label", default="agent")
        s.add_argument("--task", choices=tuple(TASKS),
                       help="Task to start; omit to reuse any active runner. reset may only confirm the active task.")
        if op in ("start", "serve"):
            s.add_argument("--preview-port", type=int, default=8766)
            s.add_argument("--preview-fps", type=float, default=8.0)
            s.add_argument("--no-preview", action="store_true")
    sub.add_parser("tasks", help="List tasks and bridge support without starting simulation")
    for op in ("status", "contract", "observe", "finish", "stop"):
        s = sub.add_parser(op)
        if op == "finish":
            s.add_argument("--reason", default="agent_finish")
    for op in ("hold", "move"):
        s = sub.add_parser(op)
        s.add_argument("--steps", type=int, default=30)
        s.add_argument("--note", default="")
        if op == "move":
            s.add_argument("--arm", choices=("right", "left"), help="Required for bimanual move")
            s.add_argument("--xyz", nargs=3, type=float)
            s.add_argument("--delta", action="store_true")
            rotation = s.add_mutually_exclusive_group()
            rotation.add_argument("--quat", nargs=4, type=float)
            rotation.add_argument("--rotvec", nargs=3, type=float)
            s.add_argument("--hand", nargs=16, type=float)
    s = sub.add_parser("act")
    s.add_argument("--file", type=Path, required=True,
                   help="JSON containing native23/46 or rotvec22/44 action(s)")
    s.add_argument("--note", default="")
    args = p.parse_args()
    if args.command == "tasks":
        print(dumps({"ok": True, "result": catalog()}))
        return
    if args.command == "serve":
        serve(args)
        return
    if args.command == "start":
        if args.task is not None:
            require_supported(args.task)
        path = Path(args.socket)
        if path.exists():
            reply = request({"op": "status"}, path)
            if not reply["ok"]:
                print(dumps(reply))
                sys.exit(1)
            active = reply["result"].get("task")
            if args.task is not None and active != args.task:
                print(dumps({"ok": False, "error": f"Active task is {active}; requested {args.task}. Confirm ownership, stop, then start --task {args.task}; no reset was performed"}))
                sys.exit(1)
            print(dumps(reply))
            return
        from preview import Preview
        if not args.no_preview and (not 1 <= args.preview_port <= 65535 or not 1 <= args.preview_fps <= 20):
            raise ValueError("Invalid preview port/fps")
        logpath = ROOT / "agent_bridge" / "runner.log"
        with logpath.open("a") as log:
            child_args = [sys.executable,str(Path(__file__).resolve()),
                          "--socket",str(path),"serve","--task",args.task or DEFAULT_TASK,
                          "--seed",str(args.seed),"--label",args.label,
                          "--preview-port",str(args.preview_port),"--preview-fps",str(args.preview_fps)]
            if args.no_preview:
                child_args.append("--no-preview")
            child = subprocess.Popen(child_args,
                      stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
        (ROOT / "agent_bridge" / "runner.pid").write_text(str(child.pid)+"\n")
        for _ in range(600):
            if child.poll() is not None:
                raise RuntimeError(f"Runner exited; inspect {logpath}")
            if path.exists():
                print(dumps(request({"op":"status"},path)))
                return
            time.sleep(0.2)
        raise RuntimeError(f"Runner not ready after 120 seconds; inspect {logpath}")
    msg = {"op": args.command}
    for k, v in vars(args).items():
        if k not in ("command", "socket", "file") and v is not None:
            msg[k] = v
    if args.command == "act":
        msg["actions"] = json.loads(args.file.read_text())
    result = request(msg, args.socket)
    print(dumps(result))
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()


