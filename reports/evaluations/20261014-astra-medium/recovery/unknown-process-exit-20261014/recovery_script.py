#!/usr/bin/env python3
"""Truthful outer recovery of three attempts whose owner processes disappeared.
This script never calls the model/bridge, replays actions, or synthesizes final results.
"""
from __future__ import annotations
import collections
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

REPO=Path('/ssd1/tianjingqi/work2/Astra_test')
sys.path.insert(0, str(REPO/'tools/evaluation'))
from run_batch import check_frozen, publish, write_json
from evidence import archive_attempt, update_batch, verify_inventory

BATCH=REPO/'reports/evaluations/20261014-astra-medium'
STATE=REPO/'dexjoco-linux-cpu-20261012/dexjoco/artifacts/evaluation-state/20261014-astra-medium'
OLD_LOG=REPO/'artifacts/evaluation-preflight/batch-20261014-astra-medium.txt'
TARGETS=[('pick_bucket','trial03-attempt01'),('pinch_tongs','trial03-attempt01'),('water_plant','trial02-attempt01')]


def utc(timestamp=None):
    return (datetime.fromtimestamp(timestamp, timezone.utc) if timestamp is not None else datetime.now(timezone.utc)).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def original_inventory(root):
    return [{'path':str(p.relative_to(root)), 'size_bytes':p.stat().st_size, 'sha256':digest(p)}
            for p in sorted(root.rglob('*')) if p.is_file()]


def active_bridges():
    found=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv=(proc/'cmdline').read_bytes().split(b'\0')
            if any(arg.endswith(b'agent_bridge/bridge.py') for arg in argv):
                found.append(int(proc.name))
        except (OSError, PermissionError):
            pass
    return found


def main():
    lock_path=Path(subprocess.check_output(['git','-C',str(REPO),'rev-parse','--absolute-git-dir'],text=True).strip())/'evaluation-scheduler.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config=json.loads((BATCH/'batch_config.json').read_text())
        check_frozen(config)
        attempts=json.loads((BATCH/'attempts.json').read_text())
        assert not active_bridges(), 'A bridge is running; recovery is refused'
        assert all(not Path('/proc',str(pid)).exists() for pid in [806072,806073])
        existing={(row['task'],row['attempt_id']) for row in attempts}
        assert not any(target in existing for target in TARGETS)
        assert subprocess.check_output(['git','-C',str(REPO),'branch','--show-current'],text=True).strip()=='test'
        old_log_rows=[]
        for line in OLD_LOG.read_text().splitlines():
            try:
                obj=json.loads(line)
                if isinstance(obj,dict): old_log_rows.append(obj)
            except json.JSONDecodeError:
                pass
        last_scheduler=old_log_rows[-1]
        support=BATCH/'recovery/unknown-process-exit-20261014'
        support.mkdir(parents=True,exist_ok=False)
        shutil.copy2(OLD_LOG,support/'scheduler-original.log')
        shutil.copy2(Path(__file__),support/'recovery_script.py')
        scheduler_evidence={
            'original_path':str(OLD_LOG),
            'archived_path':str((support/'scheduler-original.log').relative_to(BATCH)),
            'sha256':digest(OLD_LOG),
            'size_bytes':OLD_LOG.stat().st_size,
            'last_json_event':last_scheduler.get('event'),
            'last_json_time':last_scheduler.get('time'),
            'last_modification_utc':utc(OLD_LOG.stat().st_mtime),
        }
        for task,attempt_id in TARGETS:
            private=STATE/task/attempt_id
            dispatch=json.loads((private/'dispatch.json').read_text())
            commands=rows(private/'command_log.jsonl')
            launched=next(row for row in commands if row.get('request',{}).get('op')=='serve')
            pid=launched['response']['pid']
            argv=launched['command']
            sock=Path(argv[argv.index('--socket')+1])
            assert not Path('/proc',str(pid)).exists(), 'Owned process is still present'
            assert not sock.exists(), 'Socket still exists; do not replace unverified runner'
            probe=socket.socket(socket.AF_UNIX)
            probe.settimeout(1)
            try:
                probe.connect(str(sock))
            except OSError as error:
                connection={'connected':False,'exception_class':type(error).__name__,'errno':error.errno}
            else:
                raise RuntimeError('Runner socket unexpectedly accepted a connection')
            finally:
                probe.close()
            episode_dirs={row.get('response',{}).get('result',{}).get('episode_dir')
                          for row in commands if isinstance(row.get('response',{}).get('result'),dict)}
            episode_dirs.discard(None)
            assert len(episode_dirs)==1
            episode=Path(next(iter(episode_dirs)))
            assert episode.is_dir() and not (episode/'summary.json').exists()
            assert not (private/'attempt_metadata.json').exists()
            assert not (private/'recovery.json').exists()
            original_private=original_inventory(private)
            original_episode=original_inventory(episode)
            events=rows(episode/'events.jsonl')
            model_records=rows(private/'model_transcript.jsonl')
            error_count=sum(bool(row.get('returncode',0) or row.get('response',{}).get('ok') is False or row.get('exception')) for row in commands)
            last_observed=next(row['response']['result'] for row in reversed(commands)
                               if isinstance(row.get('response',{}).get('result'),dict) and 'step_count' in row['response']['result'])
            command_ops=collections.Counter(row.get('request',{}).get('op') for row in commands)
            event_types=collections.Counter(row.get('event') for row in events)
            assert command_ops['finish']==0 and command_ops['stop']==0
            recovery={
                'schema_version':1,'recovered_at':utc(),'classification':'interrupted',
                'exception_class':'UnknownProcessExit',
                'cause':'The scheduler and owned runner were absent when recovery began. No exit status or signal record survived; the external termination cause cannot be determined.',
                'missing_final_result':'The official summary.json is absent and cannot be obtained from a live runner. No final success/failure result is inferred or synthesized.',
                'replacement_required':True,'scored':False,
                'recovery_scope':'Lifecycle, record counts and evidence integrity only; no robot actions or model requests were issued.',
                'scheduler_lock':{'path':str(lock_path),'exclusive_lock_acquired':True,'held_during_archive_and_publication':True},
                'scheduler_processes':[{'pid':p,'proc_entry_exists':False} for p in [806072,806073]],
                'active_bridge_pids':active_bridges(),
                'owned_runner':{'recorded_pid':pid,'proc_entry_exists':False,'returncode':None,'termination_signal':None,
                                'socket_path':str(sock),'socket_exists':False,'connection_check':connection},
                'source_episode_dir':str(episode),'scheduler_log':scheduler_evidence,
                'last_recorded_times':{
                    'last_command_ended_at':commands[-1].get('ended_at'),
                    'last_event_wall_time':events[-1].get('wall_time'),
                    'last_event_utc':utc(events[-1]['wall_time']),
                    'last_model_event':model_records[-1].get('event'),
                    'last_model_time_unix':model_records[-1].get('time_unix'),
                    'last_model_utc':utc(model_records[-1]['time_unix']),
                },
                'last_observed_facts':{'step_count':last_observed['step_count'],'action_calls':last_observed['action_calls'],
                                       'interpretation':'These are last recorded intermediate counters, not an official final summary.'},
                'record_counts':{'commands':len(commands),'command_errors':error_count,'command_operations':dict(command_ops),
                                 'episode_events':dict(event_types),'model_events':dict(collections.Counter(row.get('event') for row in model_records))},
                'unavailable_lifecycle_evidence':['official summary.json','model_result.json','original cleanup.json','scheduler exit status','runner exit status'],
                'lifecycle_actions_taken':[],
                'preservation':{'original_attempt_files':original_private,'original_episode_files':original_episode,
                                'existing_source_files_changed':False,'official_summary_or_events_synthesized':False},
            }
            initial=json.loads((private/'initial_state_verification.json').read_text())
            assert initial['passed'] is True
            metadata_keys=['batch_id','task','trial_index','attempt_id','seed','label','replacement_of','source_commit',
                           'expected_reset_count','isolation','model','frozen_config_sha256','platform_limitations']
            metadata={key:dispatch[key] for key in metadata_keys}
            metadata.update(scored=False,status='interrupted',reason='unknown_process_exit',phase='model_execution',
                            exception_class='UnknownProcessExit',initial_state_verification=initial,
                            command_count=len(commands),command_error_count=error_count,
                            recovery={'path':'recovery.json','recorded_at':recovery['recovered_at'],'replacement_required':True,
                                      'original_cleanup_unavailable':True,'official_summary_unavailable':True})
            write_json(private/'recovery.json',recovery)
            write_json(private/'attempt_metadata.json',metadata)
            for item in original_private:
                assert digest(private/item['path'])==item['sha256']
            for item in original_episode:
                assert digest(episode/item['path'])==item['sha256']
            destination=BATCH/'episodes'/task/attempt_id
            record=archive_attempt(episode_dir=episode,attempt_dir=private,destination=destination,metadata=metadata)
            assert record['scored'] is False and record['status']=='interrupted'
            assert record['succeed'] is None and record['steps'] is None and record['action_calls'] is None
            attempts.append(record)
            write_json(BATCH/'attempts.json',attempts)
            update_batch(BATCH,config,attempts)
            verification=verify_inventory(BATCH)
            assert verification['passed'], verification
            check_frozen(config)
            commit=publish(BATCH,f'Record interrupted {task} trial {dispatch["trial_index"]} attempt 1')
            audit=json.loads((destination/'audit.json').read_text())
            print(json.dumps({'event':'interruption_recovered_and_published','task':task,'attempt_id':attempt_id,
                              'commit':commit,'scored':False,'audit_status':record['audit_status'],
                              'missing_evidence_count':audit['missing_evidence_count'],'failed_check_count':audit['failed_check_count'],
                              'inventory':verification,'scored_total':sum(row.get('scored') is True for row in attempts)},ensure_ascii=False),flush=True)
        print(json.dumps({'event':'recovery_complete','attempts':len(attempts),'scored':sum(row.get('scored') is True for row in attempts),
                          'remaining_scored':55-sum(row.get('scored') is True for row in attempts)},ensure_ascii=False),flush=True)

if __name__=='__main__':
    main()
