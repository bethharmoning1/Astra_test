#!/usr/bin/env python3
"""Verify all 11 task camera contracts and read-only previews, not policy success.
Uses a dedicated socket and preview port; never resets/stops the default runner.
"""
from pathlib import Path
from io import BytesIO
import hashlib
import http.cookiejar
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import numpy as np
import yaml
from PIL import Image
from task_registry import TASKS

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / 'agent_bridge' / 'bridge.py'
SOCKET = ROOT / 'agent_bridge' / 'camera-alignment-test.sock'
PORT = 8767
LABEL = 'camera-alignment-infrastructure'
ALL_VIEWS = ('ego_right', 'wrist', 'front', 'ego_left', 'ego', 'wrist_left', 'wrist_right')
REPORT = ROOT / 'artifacts' / 'camera_alignment_report.json'


def call(*args):
    result = subprocess.run([sys.executable, str(BRIDGE), '--socket', str(SOCKET), *args],
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Bridge failed: ' + result.stderr[-2500:] + result.stdout[-1000:])
    payload = json.loads(result.stdout)
    if not payload['ok']:
        raise RuntimeError(str(payload))
    return payload['result']


def port_open():
    with socket.socket() as s:
        s.settimeout(.3)
        return s.connect_ex(('127.0.0.1', PORT)) == 0


def check(checks, key, condition, **detail):
    checks[key] = {'passed': bool(condition), **detail}
    if not condition:
        raise AssertionError(key + ': ' + str(detail))


def cleanup_owned(pid, episode):
    if not SOCKET.exists():
        return
    status = call('status')
    if status['pid'] != pid or status['label'] != LABEL or status['episode_dir'] != episode:
        raise RuntimeError('Runner ownership changed; no stop performed')
    call('stop')
    for _ in range(100):
        if not SOCKET.exists() and not port_open():
            return
        time.sleep(.05)
    raise RuntimeError('Owned runner cleanup incomplete')


def main():
    if SOCKET.exists() or port_open():
        raise RuntimeError('Dedicated test socket/port already occupied; no runner stopped')
    report = {'purpose': 'camera_alignment_infrastructure_not_agent_task_completion',
              'agent_attempted': False, 'camera_profile': 'rand_obj', 'tasks': {}}
    owned_pid = owned_episode = None
    try:
        for name in TASKS:
            checks = {}
            record = report['tasks'][name] = {'checks': checks, 'agent_attempted': False}
            started = call('start', '--task', name, '--seed', '0', '--label', LABEL,
                           '--preview-port', str(PORT))
            owned_pid, owned_episode = started['pid'], started['episode_dir']
            record['episode_dir'] = owned_episode
            check(checks, 'new_owned_episode', started['task'] == name and started['steps'] == 0 and started['label'] == LABEL)
            path = ROOT / 'configs' / 'rand_obj' / (name + '.yaml')
            config = yaml.safe_load(path.read_text())
            mapping = config['camera_mapping']
            expected = list(mapping.values())
            record['camera_mapping'] = mapping
            fields = {'camera_mapping': mapping, 'camera_keys': expected, 'camera_profile': 'rand_obj',
                      'camera_mapping_source': 'configs/rand_obj/' + name + '.yaml',
                      'camera_config_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            contract, observed = call('contract'), call('observe')
            manifest = json.loads((Path(owned_episode) / 'manifest.json').read_text())
            for label, obj in [('status', started), ('contract', contract), ('observe', observed), ('manifest', manifest)]:
                check(checks, label + '_official_mapping', all(obj.get(k) == v for k, v in fields.items()))
            check(checks, 'two_or_three_distinct_views', len(expected) == (3 if name.startswith('bimanual_') else 2) and len(set(expected)) == len(expected))
            check(checks, 'robot_interface_unchanged', contract['native_action_dim'] == (46 if name.startswith('bimanual_') else 23))
            check(checks, 'no_privileged_state', not any(key in observed for key in ('state', 'qpos', 'object_pose', 'object_position')))
            check(checks, 'only_selected_pngs', set(p.stem for p in Path(observed['contact_sheet']).parent.glob('*.png')) == set(expected))
            check(checks, 'only_selected_image_keys', list(observed['images']) == expected)
            with Image.open(observed['contact_sheet']) as board:
                check(checks, 'selected_contact_sheet_size', board.size == (960, 505 * ((len(expected) + 1) // 2)))

            url = started['preview']['url']
            base = url.split('/?')[0]
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            with client.open(url, timeout=5) as response:
                page = response.read().decode()
                check(checks, 'authenticated_readonly_page', response.status == 200 and 'READ ONLY' in page and 'id="camera-config"' in page)
            def preview_status():
                with client.open(base + '/status', timeout=5) as r:
                    return json.load(r)
            for _ in range(100):
                status = preview_status()
                if status.get('frame_sequence', 0) > 0:
                    break
                time.sleep(.05)
            check(checks, 'preview_official_mapping', all(status.get(k) == v for k, v in fields.items()))
            check(checks, 'preview_descriptor_same_keys', started['preview']['camera_keys'] == expected)
            for camera in expected:
                with Image.open(observed['images'][camera]) as source:
                    source = np.asarray(source).astype(np.int16)
                with client.open(base + '/frame.jpg?camera=' + camera, timeout=5) as response:
                    with Image.open(BytesIO(response.read())) as image:
                        preview = np.asarray(image).astype(np.int16)
                difference = float(np.abs(source - preview).mean())
                check(checks, 'same_rgb_view_' + camera, source.shape == preview.shape == (640, 640, 3) and difference < 6,
                      mean_absolute_jpeg_difference=difference)
            with client.open(base + '/frame.jpg?camera=grid', timeout=5) as response:
                with Image.open(BytesIO(response.read())) as image:
                    check(checks, 'preview_grid_same_view_count', image.size == (960, 505 * ((len(expected) + 1) // 2)))
            for camera in set(ALL_VIEWS) - set(expected):
                for endpoint in ('frame.jpg', 'stream'):
                    code = None
                    try:
                        with client.open(base + '/' + endpoint + '?camera=' + camera, timeout=5):
                            pass
                    except urllib.error.HTTPError as exc:
                        code = exc.code
                    check(checks, 'reject_' + endpoint + '_' + camera, code == 400)
            for method in ('POST', 'PUT', 'DELETE'):
                code = None
                try:
                    with client.open(urllib.request.Request(base + '/reset', method=method), timeout=5):
                        pass
                except urllib.error.HTTPError as exc:
                    code = exc.code
                check(checks, 'reject_' + method, code == 405)
            time.sleep(.2)
            check(checks, 'readonly_poll_does_not_step', preview_status()['step_count'] == call('status')['steps'] == 0)
            after = call('hold', '--steps', '2', '--note', 'camera_interface_check_not_task_solution')
            check(checks, 'action_keeps_same_camera_contract', after['step_count'] == 2 and after['camera_mapping'] == mapping and after['camera_keys'] == expected)
            for _ in range(100):
                status = preview_status()
                if status.get('image_step_count') == 2:
                    break
                time.sleep(.05)
            check(checks, 'preview_tracks_action', status.get('image_step_count') == 2 and status['camera_keys'] == expected)
            summary = call('finish', '--reason', 'camera_alignment_infrastructure_complete')
            check(checks, 'summary_official_mapping', all(summary.get(k) == v for k, v in fields.items()))
            record['succeed'] = summary['succeed']
            cleanup_owned(owned_pid, owned_episode)
            owned_pid = owned_episode = None
            check(checks, 'dedicated_runner_cleaned', not SOCKET.exists() and not port_open())
            print(json.dumps({'task': name, 'checks_passed': len(checks), 'camera_keys': expected}), flush=True)
        report['all_passed'] = all(c['passed'] for r in report['tasks'].values() for c in r['checks'].values()) and len(report['tasks']) == 11
    except BaseException as exc:
        report['all_passed'] = False
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        if owned_pid is not None:
            cleanup_owned(owned_pid, owned_episode)
        report['runner_cleaned'] = not SOCKET.exists() and not port_open()
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'all_passed': report.get('all_passed', False), 'runner_cleaned': report['runner_cleaned'], 'report': str(REPORT)}), flush=True)


if __name__ == '__main__':
    main()
