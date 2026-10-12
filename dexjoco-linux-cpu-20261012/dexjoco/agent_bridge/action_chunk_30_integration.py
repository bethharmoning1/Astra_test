"""Isolated real-simulator checks; never uses the user runner socket or preview."""
from pathlib import Path
import hashlib
import json
import math
import traceback
import numpy as np
from scipy.spatial.transform import Rotation
from bridge import Runner, ROOT
from task_registry import require_supported

REPORT = ROOT / "artifacts" / "action_chunk_30_integration_report.json"

def upstream_hash():
    h = hashlib.sha256()
    paths = (list((ROOT / "dexjoco").rglob("*.py"))
             + list((ROOT / "dexjoco").rglob("*.xml"))
             + list((ROOT / "configs").rglob("*.yaml")))
    for p in sorted(paths):
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()

def snapshot(r):
    return {"steps": r.step_count, "sim_time": float(r.raw.data.time),
            "action_calls": r.action_calls, "obs_index": r.obs_index,
            "target": r.target.tolist(), "phase": r.phase,
            "event_bytes": (r.episode / "events.jsonl").stat().st_size}

def rotvec_action(r):
    a = r.target
    pieces = []
    for arm in r.robot.arms:
        pose = a[r.robot.poses[arm]]
        rotvec = Rotation.from_quat(pose[3:7][[1, 2, 3, 0]]).as_rotvec()
        pieces.append(np.r_[pose[:3], rotvec, a[r.robot.hands[arm]]])
    return np.concatenate(pieces)

def check_task(task):
    # A private in-process env: no default socket, preview port, or user reset.
    r = Runner(seed=0, label="infra-action-chunk-30", preview=None, task=task)
    entry = {"task": task, "episode_dir": str(r.episode),
             "purpose": "infrastructure_only_not_agent_performance", "checks": []}
    try:
        manifest_path = r.episode / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["purpose"] = entry["purpose"]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        contract = r.contract()
        assert contract["max_steps_per_request"] == 30, contract
        assert math.isclose(contract["control_dt_seconds"], .02), contract
        assert list(r.cameras) == require_supported(task)["camera_keys"]
        entry["contract"] = {k: contract[k] for k in
                             ("max_steps_per_request", "control_dt_seconds", "camera_keys", "native_action_dim")}
        bad_move = {"op": "move", "steps": 31, "xyz": [0, 0, .001], "delta": True}
        if r.dual:
            bad_move["arm"] = "right"
        rejected = [("hold31", {"op": "hold", "steps": 31}), ("move31", bad_move),
                    ("native31_steps1", {"op": "act", "steps": 1,
                     "actions": np.tile(r.target, (31, 1)).tolist()}),
                    ("rotvec31", {"op": "act", "actions": np.tile(rotvec_action(r), (31, 1)).tolist()})]
        for name, msg in rejected:
            before = snapshot(r)
            try:
                r.execute(msg)
            except ValueError as exc:
                error = str(exc)
            else:
                raise AssertionError(name + " was not rejected")
            assert before == snapshot(r), name + " advanced simulation or changed runner"
            entry["checks"].append({"name": name, "passed": True, "error": error,
                                    "state_unchanged": True})
        for name in ("hold30", "move30", "native30", "rotvec30"):
            before = snapshot(r)
            if name == "hold30":
                msg = {"op": "hold", "steps": 30}
            elif name == "move30":
                msg = {"op": "move", "steps": 30, "xyz": [0, 0, .001], "delta": True}
                if r.dual:
                    msg["arm"] = "right"
            else:
                action = rotvec_action(r) if name == "rotvec30" else r.target
                msg = {"op": "act", "actions": np.tile(action, (30, 1)).tolist()}
            obs = r.execute(msg)
            after = snapshot(r)
            assert after["steps"] - before["steps"] == 30, (name, before, after)
            assert after["action_calls"] - before["action_calls"] == 1
            elapsed = after["sim_time"] - before["sim_time"]
            assert math.isclose(elapsed, .6, abs_tol=1e-8), (name, elapsed)
            assert obs["camera_keys"] == r.cameras
            entry["checks"].append({"name": name, "passed": True, "executed_steps": 30,
                                    "sim_seconds": elapsed, "step_count": after["steps"]})
        entry["summary"] = r.finish("infrastructure_chunk_limit_test")
        entry["all_passed"] = True
        return entry
    finally:
        if not r.finished:
            r.finish("infrastructure_test_cleanup")
        r.env.close()

if __name__ == "__main__":
    report = {"all_passed": False, "purpose": "infrastructure_only_not_agent_performance", "tasks": []}
    report["upstream_hash_before"] = upstream_hash()
    try:
        for task in ("click_mouse", "bimanual_assembly"):
            result = check_task(task)
            report["tasks"].append(result)
            print(json.dumps({"task": task, "all_passed": True,
                              "checks": len(result["checks"]), "episode_dir": result["episode_dir"]}), flush=True)
        report["upstream_hash_after"] = upstream_hash()
        report["upstream_unchanged"] = report["upstream_hash_before"] == report["upstream_hash_after"]
        assert report["upstream_unchanged"], "Unexpected upstream source changes"
        report["all_passed"] = True
    except BaseException:
        report["error"] = traceback.format_exc()
        raise
    finally:
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"all_passed": report["all_passed"], "report": str(REPORT)}), flush=True)
