#!/usr/bin/env python3
"""Completion-driven legacy drain witness. Never activates/stops a unit.

Consumes only trusted OLD controller journal lifecycle messages, pidfds and
cgroup populated notifications. No timers/status/API polling, console/JIT/env
reads, inferred job success, automatic rollback or new claims.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import hashlib
import stat
import subprocess


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--operator-source',required=True);args=parser.parse_args()
    source=Path(args.operator_source)
    if source.parent!=Path('/nix/store') or source.resolve(strict=True)!=source or source.lstat().st_uid!=0 or source.stat().st_mode&0o222:
        raise RuntimeError('Only the reviewed immutable canonical operator source is accepted')
    manifest_file=Path('/var/lib/hound-ci/rollout-cache-v2-20261005/manifest.json')
    if manifest_file.stat().st_size>65536:raise RuntimeError('Manifest bound exceeded')
    initial=json.loads(manifest_file.read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest()!=initial['operator_sha256']:
        raise RuntimeError('Operator source differs from the armed reviewed source')
    spec=importlib.util.spec_from_file_location('drain',source)
    drain=importlib.util.module_from_spec(spec);spec.loader.exec_module(drain)
    path=drain.STATE/'manifest.json'
    if path.stat().st_size>65536:raise RuntimeError('Manifest format bound exceeded')
    manifest=json.loads(path.read_text())
    if manifest['phase']!='armed-awaiting-job-completion':raise RuntimeError('All gates must be positively armed first')
    if {a['slot'] for a in manifest['armed']}!={1,2,3,4}:raise RuntimeError('Not all gates positively armed')
    entries=manifest['controllers'];assert {e['slot'] for e in entries}=={1,2,3,4}
    journal=subprocess.Popen(['journalctl','--utc','--no-pager','--follow','--output=json','--since',manifest['created_utc'],*[item for e in entries for item in ('-u',f'hound-ci-{e["slot"]}.service')]],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    poller=select.poll();poller.register(journal.stdout.fileno(),select.POLLIN|select.POLLHUP)
    fds={};state={};buffer=b''
    def check(e):
        slot=e['slot'];item=state[slot]
        values=drain.properties(slot)
        if values['Restart']!='no':raise RuntimeError('Drain hold unexpectedly removed')
        if values['MainPID']!='0':return
        group=Path('/sys/fs/cgroup')/e['control_group'].lstrip('/')
        if group.exists() and (group/'cgroup.procs').read_text().strip():return
        item['controller_exited']=True;item['cgroup_empty']=True
        # Actual accepted VM must have positively completed, even a failed CI
        # job. Never infer a PASS outcome or completion from disappearance.
        if not item.get('completed_vm'):return
        registration=drain.public_registration(slot)
        if registration is not None and registration['id'] is not None:
            raise RuntimeError('Positive old registration remains: cleanup not witnessed')
        item['registration_recovery']=registration
        if not item.get('drained'):
            item['drained']=True
            print(f'HOUND_CI_SLOT_DRAINED slot={slot} old-pid={e["pid"]} completed-vm=true cgroup-empty=true',flush=True)
            manifest['drain_witness']=state;drain.save(manifest)
    try:
        for e in entries:
            slot=e['slot'];assert e['control_group']==f'/hound-ci.slice/hound-ci-{slot}.service'
            state[slot]={'controller_exited':False,'cgroup_empty':False,'completed_vm':False,'drained':False}
            try:
                pidfd=os.pidfd_open(e['pid'])
                if drain.starttime(e['pid'])!=e['starttime']:os.close(pidfd);raise RuntimeError('Old PID reused')
                poller.register(pidfd,select.POLLIN);fds[pidfd]=('pid',e)
            except ProcessLookupError:
                if drain.properties(slot)['MainPID']!='0':raise RuntimeError('Old PID absent but service replacement exists')
            events=Path('/sys/fs/cgroup')/e['control_group'].lstrip('/')/'cgroup.events'
            if events.exists():
                fd=os.open(events,os.O_RDONLY);os.read(fd,4096)
                poller.register(fd,select.POLLPRI|select.POLLERR);fds[fd]=('cgroup',e)
            check(e)
        while not all(item['drained'] for item in state.values()):
            # Kernel wait for process/cgroup/journal events; no periodic wakes.
            for fd,event in poller.poll():
                if fd==journal.stdout.fileno():
                    block=os.read(fd,65536)
                    if not block:raise RuntimeError('Journal stream ended before all drain witnesses')
                    buffer+=block
                    while b'\n' in buffer:
                        line,buffer=buffer.split(b'\n',1)
                        if len(line)>65536:continue
                        row=json.loads(line)
                        for e in entries:
                            if row.get('_PID')!=str(e['pid']) or row.get('_SYSTEMD_UNIT')!=f'hound-ci-{e["slot"]}.service':continue
                            message=row.get('MESSAGE','')
                            prefix=f'slot={e["slot"]} VM stopped; preflight='
                            if prefix in message:
                                if 'preflight=True; runner completed=True; erasing disk' not in message:
                                    raise RuntimeError('Old VM did not positively complete; preserve actual failure')
                                state[e['slot']]['completed_vm']=True;check(e)
                else:
                    kind,e=fds[fd]
                    if kind=='cgroup':
                        try:os.lseek(fd,0,os.SEEK_SET);os.read(fd,4096)
                        except OSError:event |= select.POLLERR
                        if event & (select.POLLERR|select.POLLHUP):
                            poller.unregister(fd);os.close(fd);del fds[fd]
                    else:poller.unregister(fd);os.close(fd);del fds[fd]
                    check(e)
        manifest['phase']='all-four-drained-awaiting-replacement';manifest['drained_utc']=drain.timestamp();manifest['drain_witness']=state;drain.save(manifest)
        print('HOUND_CI_ALL_FOUR_DRAINED no-busy-job-kill no-old-reclaim',flush=True)
    finally:
        journal.terminate();journal.wait(timeout=10)
        for fd in fds:os.close(fd)


if __name__=='__main__':main()
