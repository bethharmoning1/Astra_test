"""Simulation infrastructure checks, NOT an agent performance evaluation.
The last success-predicate fixture deliberately uses privileged state in an
isolated environment and must never be counted as a policy success.
"""
import os
os.environ.setdefault("MUJOCO_GL", "egl")
os.environ.setdefault("MUJOCO_EGL_DEVICE_ID", "1")
from pathlib import Path
import json
import random
import numpy as np
import mujoco
import gymnasium
from scipy.spatial.transform import Rotation
from bridge import Runner

ROOT=Path(__file__).resolve().parent.parent
report={"kind":"simulation_smoke_test_not_policy_evaluation","checks":{},
 "versions":{"mujoco":mujoco.__version__,"gymnasium":gymnasium.__version__,"numpy":np.__version__}}
r=Runner(0,"smoke")
try:
 c=report["checks"]
 o=r.obs
 c["reset"]={"passed":o["state"].shape==(31,) and not r.info["succeed"],"raw_state_dim":len(o["state"]),"agent_state_dim":23}
 first=o["state"].copy()
 c["cameras"]={"passed":all(o[k].shape==(640,640,3) and o[k].dtype==np.uint8 and float(o[k].std())>5 for k in ["front","ego_left","ego_right","wrist"]),"shapes":{k:list(o[k].shape) for k in ["front","ego_left","ego_right","wrist"]}}
 r.reset(0,"smoke-repeat")
 c["seed_repeatability"]={"passed":bool(np.allclose(first,r.obs["state"],atol=1e-10)),"max_error":float(np.max(np.abs(first-r.obs["state"])))}
 initial=r.obs["state"][:7].copy()
 for _ in range(2):
  r.execute({"op":"hold","steps":30,"note":"simulation smoke: maintain reset pose (two short chunks)"})
 drift=float(np.linalg.norm(r.obs["state"][:3]-initial[:3]))
 c["hold"]={"passed":bool(drift<0.02 and np.all(np.isfinite(r.raw.data.qpos))),"tcp_drift_m":drift,"steps":60}
 start=r.obs["state"][:3].copy()
 r.execute({"op":"move","xyz":[0.015,0,0],"delta":True,"steps":30,"note":"simulation smoke: small positive x motion"})
 r.execute({"op":"hold","steps":25,"note":"simulation smoke: settle"})
 actual=r.obs["state"][:3]-start
 c["action_execution"]={"passed":bool(actual[0]>0.005 and np.linalg.norm(actual-np.array([0.015,0,0]))<0.025),"command_delta_m":[0.015,0,0],"actual_delta_m":actual.tolist()}
 target=r.target.copy()
 rotvec=Rotation.from_quat(target[3:7][[1,2,3,0]]).as_rotvec()
 a22=np.r_[target[:3],rotvec,target[7:]]
 r.execute({"op":"act","actions":[a22.tolist()],"note":"simulation smoke: 22D rotvec conversion"})
 c["rotvec_22_to_quat_23"]={"passed":bool(np.allclose(r.target[:3],target[:3]) and abs(np.dot(r.target[3:7],target[3:7]))>0.999999)}
 before=r.step_count
 try:
  r.execute({"op":"move","xyz":[99,0,1],"steps":1})
  rejected=False
 except ValueError:
  rejected=True
 c["invalid_action_rejected"]={"passed":rejected and before==r.step_count}
 c["normal_success_negative"]={"passed":not r.success,"upstream_succeed":r.success}
 # Predicate unit fixture only. No policy is running and this episode is smoke.
 raw=r.raw
 raw._data.jnt("mouse_root").qpos[:3]=raw._data.geom_xpos[raw._mousepad_geom_id].copy()
 raw._data.jnt("mouse_joint_0").qpos[0]=raw._mouse_joint0_init+0.002
 mujoco.mj_forward(raw.model,raw.data)
 in_pad=bool(raw._mouse_in_mousepad())
 clicked=bool(raw._detect_click())
 raw._success_trigger_count=0
 raw._display_blue=False
 not_clicked_success=bool(raw._compute_success())
 if in_pad and clicked:
  raw._set_display_blue()
 flags=[bool(raw._compute_success()) for _ in range(raw._success_trigger_target)]
 c["success_predicate_fixture"]={"passed":in_pad and clicked and not not_clicked_success and not any(flags[:-1]) and flags[-1],"privileged_fixture_not_agent":True,"in_pad":in_pad,"clicked":clicked,"consecutive_checks":flags,"target":raw._success_trigger_target}
 report["all_passed"]=all(v["passed"] for v in c.values())
 report["artifact_dir"]=str(r.episode)
 r.finish("smoke_complete_not_policy_score")
finally:
 r.env.close()
path=ROOT/"artifacts"/"smoke_report.json"
path.write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report))
if not report["all_passed"]:
 raise SystemExit(1)
