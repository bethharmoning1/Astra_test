"""Remote integration check; infrastructure test, not an agent task attempt."""
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
ROOT=Path(__file__).resolve().parent.parent
BRIDGE=ROOT/"agent_bridge"/"bridge.py"
SOCKET=ROOT/"agent_bridge"/"runner.sock"
checks={}
report={"kind":"read_only_preview_integration_not_agent_score","checks":checks}

def call(*args):
    p=subprocess.run([sys.executable,str(BRIDGE),*args],capture_output=True,text=True,timeout=180)
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    r=json.loads(p.stdout)
    if not r["ok"]:raise RuntimeError(str(r))
    return r["result"]

def state(o):
    return np.array(o["tcp_pose_wxyz"]+o["hand_joints"])

def wait_stopped():
    for _ in range(100):
        if not SOCKET.exists():return
        time.sleep(.05)
    raise RuntimeError("Runner did not clean up socket")

if SOCKET.exists():raise SystemExit("Runner active; refuse to disturb another episode")
try:
    call("start","--seed","42","--label","preview-baseline","--no-preview")
    call("hold","--steps","20")
    call("move","--xyz","0.015","0","0","--delta","--steps","30","--note","preview_comparison")
    baseline=call("hold","--steps","20")
    call("finish","--reason","preview_baseline_complete")
    call("stop");wait_stopped()
    started=call("start","--seed","42","--label","preview-integration")
    url=started["preview"]["url"]
    base=url.split("/?")[0]
    client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with client.open(url,timeout=5) as response:
        checks["authenticated_page"]={"passed":response.status==200 and b"READ ONLY" in response.read()}
    def get_status():
        with client.open(base+"/status",timeout=5) as response:return json.load(response)
    for _ in range(100):
        before=get_status()
        if before.get("frame_sequence",0)>0:break
        time.sleep(.05)
    time.sleep(.8)
    paused=get_status()
    checks["read_does_not_advance"]={"passed":before["step_count"]==paused["step_count"]==0
                                   and before["sim_time"]==paused["sim_time"]
                                   and before["frame_sequence"]==paused["frame_sequence"]}
    for method in ("POST","PUT","DELETE"):
        try:client.open(urllib.request.Request(base+"/reset",method=method),timeout=3)
        except urllib.error.HTTPError as exc:
            checks["reject_"+method]={"passed":exc.code==405}
    try:urllib.request.urlopen(base+"/status",timeout=3)
    except urllib.error.HTTPError as exc:checks["unauthenticated_rejected"]={"passed":exc.code==401}
    camera_shapes={}
    for camera in (*baseline["camera_keys"], "grid"):
        with client.open(base+"/frame.jpg?camera="+camera,timeout=5) as response:
            image=Image.open(BytesIO(response.read()))
            camera_shapes[camera]=list(image.size)
            if float(np.asarray(image).std())<5:raise RuntimeError("Blank camera")
    checks["cameras"]={"passed":True,"sizes":camera_shapes}
    with client.open(base+"/stream?camera=wrist",timeout=5) as response:
        first=response.readline().strip()
        headers={}
        while True:
            line=response.readline().strip()
            if not line:break
            key,value=line.decode().split(":",1);headers[key]=value.strip()
        image=Image.open(BytesIO(response.read(int(headers["Content-Length"]))))
        checks["mjpeg_stream"]={"passed":first==b"--dexjoco" and image.size==(640,640)}
    call("hold","--steps","20")
    actor=subprocess.Popen([sys.executable,str(BRIDGE),"move","--xyz","0.015","0","0","--delta",
                            "--steps","30","--note","preview_comparison"],stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,text=True)
    samples=[]
    t0=time.monotonic()
    while actor.poll() is None:
        s=get_status()
        samples.append({"wall_elapsed":time.monotonic()-t0,"step":s["step_count"],
                        "image_step":s.get("image_step_count"),"frame":s.get("frame_sequence"),
                        "phase":s["phase"]})
        time.sleep(.05)
    stdout,stderr=actor.communicate(timeout=10)
    if actor.returncode:raise RuntimeError(stdout+stderr)
    last=call("hold","--steps","20")
    after=get_status()
    intermediate=[s for s in samples if s["phase"]=="executing" and isinstance(s["image_step"], int) and 20<s["image_step"]<50]
    checks["intermediate_frames_during_action"]={"passed":len(set(s["image_step"] for s in intermediate))>2,
            "unique_image_steps":sorted(set(s["image_step"] for s in intermediate))}
    difference=float(np.max(np.abs(state(last)-state(baseline))))
    checks["same_actions_same_robot_result"]={"passed":difference<1e-10 and last["succeed"]==baseline["succeed"],
            "max_proprio_difference":difference,"baseline_steps":baseline["step_count"],
            "preview_steps":last["step_count"]}
    time.sleep(.4)
    paused=get_status();time.sleep(.5);paused2=get_status()
    checks["pause_after_actions"]={"passed":paused["phase"]=="paused" and paused2["phase"]=="paused"
                                   and paused["step_count"]==paused2["step_count"]==70
                                   and paused["frame_sequence"]==paused2["frame_sequence"]}
    summary=call("finish","--reason","preview_integration_complete")
    checks["finish_state"]={"passed":get_status()["phase"]=="finished"}
    stopped=call("stop");wait_stopped()
    checks["finish_stop_idempotent"]={"passed":summary==stopped}
    try:urllib.request.urlopen(base+"/status",timeout=1)
    except urllib.error.URLError:checks["preview_port_cleaned"]={"passed":True}
    report["samples"]=samples
    report["motion_wall_seconds"]=samples[-1]["wall_elapsed"] if samples else 0
    report["all_passed"]=all(check["passed"] for check in checks.values())
finally:
    if SOCKET.exists():call("stop")
path=ROOT/"artifacts"/"preview_integration_report.json"
path.write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps({"all_passed":report["all_passed"],"checks":checks,"report":str(path)}))
if not report["all_passed"]:raise SystemExit(1)
