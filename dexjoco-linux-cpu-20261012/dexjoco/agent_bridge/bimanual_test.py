#!/usr/bin/env python3
"""Five-task infrastructure validation, not GPT success scores.
Only RGB and robot state are read; no task/object tail or solution trajectories.
"""
import http.cookiejar
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation
import bridge
from task_registry import catalog
ROOT=Path(__file__).resolve().parent.parent
REPORT=ROOT/'artifacts'/'bimanual_report.json'
SOCKET=bridge.DEFAULT_SOCKET
report={'kind':'five_bimanual_tasks_infrastructure_only_not_agent_score','tasks':{}}
owned_pid=None

def check(checks,name,condition,**details):
    checks[name]={'passed':bool(condition),**details}
    if not condition: raise AssertionError(name+': '+str(details))

def req(op,**args):
    reply=bridge.request({'op':op,**args})
    if not reply['ok']: raise RuntimeError(reply['error'])
    return reply['result']

def cli(*args):
    p=subprocess.run([sys.executable,str(ROOT/'agent_bridge'/'bridge.py'),*args],capture_output=True,text=True,timeout=180)
    if p.returncode: raise RuntimeError(p.stdout+p.stderr)
    return json.loads(p.stdout)['result']

def stop_owned():
    global owned_pid
    if owned_pid is not None and SOCKET.exists():
        if req('status')['pid']!=owned_pid: raise RuntimeError('Different runner; not stopping')
        req('stop')
        for _ in range(200):
            if not SOCKET.exists(): break
            time.sleep(.05)
        if SOCKET.exists(): raise RuntimeError('Socket cleanup timeout')
    owned_pid=None

def bad(checks,name,message):
    before=req('status')
    reply=bridge.request(message)
    after=req('status')
    check(checks,name,not reply['ok'] and before['steps']==after['steps'] and before['sim_time']==after['sim_time'])

def rotated44(a):
    def arm(p,h):
        return np.r_[p[:3],Rotation.from_quat(p[3:7][[1,2,3,0]]).as_rotvec(),h]
    return np.r_[arm(a[:7],a[14:30]),arm(a[7:14],a[30:46])].tolist()

if sys.platform!='linux': raise SystemExit('Run in the isolated remote venv')
if SOCKET.exists(): raise SystemExit('Existing runner; refusing to disturb it')
try:
    for item in catalog()['tasks']:
        if item['robot_type']!='dual_arm': continue
        name=item['task']; c={}
        t=report['tasks'][name]={'checks':c,'agent_attempted':False}
        start=cli('start','--task',name,'--seed','0','--label','bimanual-smoke')
        owned_pid=start['pid']; episode=Path(start['episode_dir']); t['episode_dir']=str(episode)
        check(c,'identity',start['task']==name and start['native_action_dim']==46 and start['steps']==0)
        contract=req('contract'); first=req('observe'); initial=np.array(first['robot_state'])
        check(c,'native46_robot_only',initial.shape==(46,) and np.all(np.isfinite(initial)) and not any(k in first for k in ('state','qpos','object_pose','info')))
        check(c,'official_action_layout',contract['native_action_dim']==46 and contract['rotvec_action_dim']==44
              and first['right_tcp_pose_wxyz']==initial[:7].tolist() and first['left_tcp_pose_wxyz']==initial[7:14].tolist()
              and first['right_hand_joints']==initial[14:30].tolist() and first['left_hand_joints']==initial[30:46].tolist())
        check(c,'left_right_hand_order',contract['hand_order']['right'][0]=='ffj0_right' and contract['hand_order']['left'][0]=='rfj0_left')
        check(c,'three_native_cameras',first['camera_keys']==['ego','wrist_left','wrist_right'])
        for cam,ip in first['images'].items():
            image=np.array(Image.open(ip)); check(c,'rgb_'+cam,image.shape==(640,640,3) and image.dtype==np.uint8 and image.std()>5)
        client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        url=start['preview']['url']; base=url.split('/?')[0]
        with client.open(url,timeout=5) as r: page=r.read().decode()
        check(c,'dynamic_viewer_buttons',all('data-camera="'+k+'"' in page for k in item['camera_keys']) and 's.task_description_zh||s.prompt' in page)
        def ps():
            with client.open(base+'/status',timeout=5) as r: return json.load(r)
        for _ in range(100):
            preview=ps()
            if preview.get('frame_sequence',0)>0: break
            time.sleep(.05)
        check(c,'preview_goal_and_robot_only',preview['task']==name and preview['prompt']==item['prompt'] and len(preview['robot_state'])==46)
        for cam in (*item['camera_keys'],'grid'):
            with client.open(base+'/frame.jpg?camera='+cam,timeout=5) as r: image=Image.open(BytesIO(r.read()))
            check(c,'preview_'+cam,image.size==((960,1010) if cam=='grid' else (640,640)))
        try: urllib.request.urlopen(base+'/status',timeout=5); raise AssertionError('Unauthenticated access accepted')
        except urllib.error.HTTPError as e: check(c,'preview_auth',e.code==401)
        try: client.open(urllib.request.Request(base+'/act',data=b'{}'),timeout=5); raise AssertionError('Write accepted')
        except urllib.error.HTTPError as e: check(c,'preview_read_only',e.code==405)
        time.sleep(.2)
        check(c,'paused_clock',req('status')['sim_time']==first['sim_time'] and req('status')['steps']==0)
        bad(c,'move_requires_arm',{'op':'move','xyz':[.001,0,0],'delta':True,'steps':2})
        bad(c,'reset_cannot_switch',{'op':'reset','task':'click_mouse','label':'wrong'})
        hold=req('hold',steps=6,note='infra_hold')
        check(c,'both_arms_hold',hold['step_count']==6 and np.linalg.norm(np.array(hold['right_tcp_pose_wxyz'])[:3]-initial[:3])<.02 and np.linalg.norm(np.array(hold['left_tcp_pose_wxyz'])[:3]-initial[7:10])<.02)
        prior=np.array(hold['last_target_46'])
        right=cli('move','--arm','right','--xyz','.003','0','0','--delta','--steps','8','--note','infra_right')
        a=np.array(right['last_target_46'])
        check(c,'right_move_left_target_unchanged',right['step_count']==14 and np.allclose(a[7:14],prior[7:14],rtol=0,atol=1e-14) and np.array_equal(a[30:46],prior[30:46]) and abs(a[0]-hold['right_tcp_pose_wxyz'][0]-.003)<1e-10)
        check(c,'right_moves_measured',np.linalg.norm(np.array(right['right_tcp_pose_wxyz'])[:3]-np.array(hold['right_tcp_pose_wxyz'])[:3])>1e-5)
        left=cli('move','--arm','left','--xyz','.003','0','0','--delta','--steps','8','--note','infra_left')
        b=np.array(left['last_target_46'])
        check(c,'left_move_right_target_unchanged',left['step_count']==22 and np.allclose(b[:7],a[:7],rtol=0,atol=1e-14) and np.array_equal(b[14:30],a[14:30]) and abs(b[7]-right['left_tcp_pose_wxyz'][0]-.003)<1e-10)
        check(c,'left_moves_measured',np.linalg.norm(np.array(left['left_tcp_pose_wxyz'])[:3]-np.array(right['left_tcp_pose_wxyz'])[:3])>1e-5)
        hand=b[30:46].copy(); hand[1]+=.01
        fingers=req('move',arm='left',hand=hand.tolist(),steps=3)
        b=np.array(fingers['last_target_46'])
        check(c,'left_hand_target',abs(b[31]-hand[1])<1e-12 and np.array_equal(b[14:30],a[14:30]))
        # Both poses changed in a native46 request (not two23 concatenation).
        native=b.copy(); native[0]+=.001; native[7]+=.001
        file=episode/'native46.json'; file.write_text(json.dumps([native.tolist(),native.tolist()]))
        act=cli('act','--file',str(file)); check(c,'native46_sync_act',act['step_count']==27 and np.allclose(act['last_target_46'],native,atol=1e-12))
        rv=req('act',actions=rotated44(native)); check(c,'rotvec44_right22_left22',rv['step_count']==28 and np.allclose(rv['last_target_46'],native,atol=1e-10))
        invalid=native.copy(); invalid[7]=99
        bad(c,'entire_sequence_invalid_left_workspace_no_step',{'op':'act','actions':[native.tolist(),invalid.tolist()]})
        invalid=native.copy(); invalid[30]=contract['hand_joint_limits_rad']['left'][0][1]+1
        bad(c,'invalid_left_joint_no_step',{'op':'act','actions':invalid.tolist()})
        invalid=native.copy(); invalid[10:14]=0
        bad(c,'invalid_left_quaternion_no_step',{'op':'act','actions':invalid.tolist()})
        bad(c,'single23_rejected_no_step',{'op':'act','actions':native[:23].tolist()})
        check(c,'official_success_boolean',isinstance(rv['succeed'],bool) and isinstance(rv['terminated'],bool))
        summary=req('finish',reason='bimanual_infrastructure_only')
        saved=json.loads((episode/'summary.json').read_text()); manifest=json.loads((episode/'manifest.json').read_text())
        check(c,'summary_manifest',saved==summary and saved['task']==name and saved['native_action_dim']==46 and saved['formal_benchmark'] is False and manifest['observation_fields']==['rgb','state[:46]'] and manifest['privileged_state_exposed'] is False)
        reset=req('reset',seed=0,label='bimanual-reset')
        check(c,'reset_reproducible',reset['step_count']==0 and np.allclose(reset['robot_state'],initial,atol=1e-8,rtol=0))
        # Exact same low-level native actions with preview enabled/disabled.
        fixed=np.array(reset['last_target_46']); fixed[0]+=.001; fixed[7]+=.001
        end=req('act',actions=[fixed.tolist()]*12)
        req('finish',reason='preview_motion_consistency'); stop_owned()
        start2=cli('start','--task',name,'--seed','0','--label','bimanual-no-preview','--no-preview'); owned_pid=start2['pid']
        end2=req('act',actions=[fixed.tolist()]*12)
        diff=float(np.max(np.abs(np.array(end2['robot_state'])-np.array(end['robot_state']))))
        check(c,'preview_does_not_change_motion',diff==0,max_robot_state_diff=diff)
        req('finish',reason='preview_disabled_infrastructure_only'); stop_owned()
        check(c,'socket_cleanup',not SOCKET.exists())
        t['all_passed']=all(x['passed'] for x in c.values())
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'task':name,'checks':len(c),'all_passed':t['all_passed']}),flush=True)
    report['all_passed']=len(report['tasks'])==5 and all(t['all_passed'] for t in report['tasks'].values())
except Exception as exc:
    report['error']=repr(exc); report['all_passed']=False
    raise
finally:
    stop_owned()
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'report':str(REPORT),'all_passed':report.get('all_passed',False)}),flush=True)
