#!/usr/bin/env python3
"""Host-free tests: never register runners, launch QEMU, or alter nftables."""
import importlib.util
import json
import ast
import os
import subprocess
import tempfile
import stat
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('supervisor', Path(__file__).with_name('supervisor.py'))
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


SYSTEM = '/nix/store/' + 'a' * 32 + '-nixos-system-hound-ci-26.11pre-git'
HELPER = '/nix/store/x-hound-ci/bin/hound-ci'
NSPAWN = '/nix/store/x-systemd/bin/systemd-nspawn'
PATHS = '/nix/store/' + 'c' * 32 + '-closure-info/store-paths'


class SupervisorTests(unittest.TestCase):
    def test_firewall_collapses_connected_routes_and_checks_each_uid(self):
        commands = []
        rules = []
        def fake_run(argv, **kwargs):
            commands.append(argv)
            if argv[0] == 'ip':
                routes = [{'dst': '192.168.1.0/24'}, {'dst': '172.17.0.0/16'}] if '-4' in argv else [{'dst': 'fc00::/64'}]
                return SimpleNamespace(stdout=json.dumps(routes).encode())
            if argv[0] == 'nft':
                rules.append(kwargs['input'].decode())
            if argv[0] == 'setpriv':
                # A syntactically valid child program must fail only because
                # connectivity was denied, not due to a Python quoting mistake.
                compile(argv[-2], '<connect-test>', 'exec')
            return SimpleNamespace(stdout=b'')
        with patch.object(supervisor, 'run', side_effect=fake_run), patch.object(supervisor.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1234, pw_gid=1234)), patch.object(supervisor, 'message'):
            supervisor.firewall(SimpleNamespace(count=4))
        self.assertEqual(len([c for c in commands if c[0] == 'setpriv']), 5)
        self.assertEqual(len(rules), 2)
        self.assertIn('fib daddr type local counter reject', rules[0])
        self.assertIn('100.64.0.0/10', rules[0])
        self.assertNotIn('192.168.1.0/24', rules[0])
        self.assertNotIn('172.17.0.0/16', rules[0])
        self.assertIn('destroy table inet hound_ci', rules[0])
        self.assertNotIn('flush ruleset', rules[0])
        # Job chains for the pool and the canary slot, whatever the count.
        self.assertIn('iifname "ve-hci-job-5" ip saddr != 10.231.5.2 counter drop', rules[0])
        self.assertIn('iifname "ve-hci-job-4" ip saddr != 10.231.4.2 counter drop', rules[0])

    def test_registration_recovery_is_exact_and_preserved_on_api_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'slot-1-registration.json'
            supervisor.save_record(path, {'repo': 'xmit-dev/ultimator', 'id': 321, 'name': 'hound-ci-1-test'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            # 422 (GitHub still sees the runner busy) or 5xx: never raises; the
            # record goes aside, kept, and later iterations retry it.
            for status, runner in ((b'HTTP 422', 321), (b'HTTP 503', 322)):
                supervisor.save_record(path, {'repo': 'xmit-dev/ultimator', 'id': runner, 'name': 'hound-ci-1-test'})
                failure = subprocess.CalledProcessError(1, ['gh'], stderr=status)
                with patch.object(supervisor, 'gh', side_effect=failure), patch.object(supervisor, 'message'):
                    self.assertFalse(supervisor.cleanup_record(path, 'xmit-dev/ultimator'))
                self.assertFalse(path.exists())
                aside = Path(root) / f'slot-1-stale-{runner}.json'
                self.assertEqual(json.loads(aside.read_text())['id'], runner)
            with patch.object(supervisor, 'STATE', Path(root)), patch.object(supervisor, 'message'), \
                    patch.object(supervisor, 'gh', side_effect=[subprocess.CalledProcessError(1, ['gh'], stderr=b'HTTP 422'), None]) as api:
                supervisor.retry_stale(1, 'xmit-dev/ultimator')
            self.assertEqual([c.args[0] for c in api.call_args_list],
                             [['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/321'],
                              ['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/322']])
            self.assertTrue((Path(root) / 'slot-1-stale-321.json').exists())  # still busy: kept in place
            self.assertFalse((Path(root) / 'slot-1-stale-322.json').exists())  # deleted: dropped
            with patch.object(supervisor, 'STATE', Path(root)), \
                    patch.object(supervisor, 'gh', side_effect=subprocess.CalledProcessError(1, ['gh'], stderr=b'HTTP 404')):
                supervisor.retry_stale(1, 'xmit-dev/ultimator')
            self.assertFalse((Path(root) / 'slot-1-stale-321.json').exists())  # GitHub forgot it: dropped
            supervisor.save_record(path, {'repo': 'xmit-dev/ultimator', 'id': 323, 'name': 'hound-ci-1-test'})
            with patch.object(supervisor, 'gh', return_value=None) as api:
                self.assertTrue(supervisor.cleanup_record(path, 'xmit-dev/ultimator'))
            api.assert_called_once_with(['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/323'])
            self.assertFalse(path.exists())

    def test_uncertain_post_recovers_only_exact_owned_name(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'registration.json'
            supervisor.save_record(path, {'repo': 'xmit-dev/ultimator', 'id': None, 'name': 'hound-ci-1-exact'})
            with patch.object(supervisor, 'gh', side_effect=[[{'runners': [{'id': 1, 'name': 'hound-ci-1-exact'}, {'id': 2, 'name': 'hound-ci-1-someone-else'}]}], None]) as api:
                supervisor.cleanup_record(path, 'xmit-dev/ultimator')
            self.assertEqual(api.call_args_list[-1].args[0], ['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/1'])
            self.assertFalse(path.exists())

    def test_chmod_is_done_by_owner_without_extra_capabilities(self):
        code = Path(__file__).with_name('supervisor.py').read_text()
        self.assertLess(code.index('path.chmod(0o600)'), code.index('os.chown(path, account.pw_uid, account.pw_gid)'))
        self.assertLess(code.index('os.chown(disk, 0, 0)'), code.index('disk.chmod(0o444)'))

    def test_live_vm_attestation_rejects_unknown_or_preexec(self):
        account = SimpleNamespace(pw_uid=123, pw_gid=124)
        fields = {'Name': '.qemu-system-x8', 'Uid': '123 123 123 123', 'Gid': '124 124 124 124', 'Groups': '125', 'NoNewPrivs': '1', 'Seccomp': '2', **{key: '0000000000000000' for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')}}
        self.assertTrue(supervisor.vm_security_ok(fields, account, 125))
        self.assertFalse(supervisor.vm_security_ok({}, account, 125))
        self.assertFalse(supervisor.vm_security_ok(fields | {'Name':'setpriv'}, account, 125))
        self.assertFalse(supervisor.vm_security_ok(fields | {'CapEff':'0000000000000080'}, account, 125))
        self.assertFalse(supervisor.vm_security_ok(fields | {'Uid':'0 0 0 0'}, account, 125))
        self.assertFalse(supervisor.vm_security_ok(fields | {'Groups':'0 125'}, account, 125))

    def test_guest_wrapper_preserves_function_failure_and_refuses_sealing(self):
        tree = ast.parse(Path(__file__).with_name('supervisor.py').read_text())
        script = next(ast.literal_eval(node.value) for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'seal_script' for t in node.targets))
        for child in ["failure(){ echo actual-fetch-failure >&2; return 6; }; failure\n", 'echo "$missing_variable"\n']:
            with tempfile.TemporaryDirectory() as root:
                folder=Path(root)
                stub=folder/'provision.sh'; stub.write_text(child)
                clean=folder/'cloud-init'; clean.write_text('#!/bin/bash\ntouch "'+str(folder/'incorrectly-cleaned')+'"\n'); clean.chmod(0o755)
                wrapper=script.replace('/var/log/hound-ci-provision.log',str(folder/'provision.log')).replace('/var/log/hound-ci-provision.result',str(folder/'result')).replace('/root/provision-ci.sh',str(stub)).replace('/dev/ttyS0',str(folder/'serial'))
                path=folder/'wrapper.sh'; path.write_text(wrapper)
                env=os.environ.copy(); env['PATH']=root+':'+env['PATH']
                run=subprocess.run(['bash',str(path)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                self.assertNotEqual(run.returncode,0)
                self.assertIn(f'status={run.returncode}',(folder/'result').read_text())
                self.assertTrue((folder/'provision.log').read_text())
                self.assertNotIn('HOUND_CI_IMAGE_SEALED_OK',(folder/'serial').read_text())
                self.assertFalse((folder/'incorrectly-cleaned').exists())

    def test_storage_admission_fails_closed(self):
        with patch.object(supervisor.os, 'statvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)):
            with self.assertRaises(RuntimeError):
                supervisor.admission(128)
        with patch.object(supervisor.os, 'statvfs', return_value=SimpleNamespace(f_bavail=512*1024**3, f_frsize=1)):
            supervisor.admission(128)

    def test_pins_are_immutable_hashes(self):
        self.assertEqual(len(supervisor.IMAGE_SHA256), 64)
        self.assertEqual(int(supervisor.IMAGE_SHA256, 16).bit_length() > 0, True)
        provision = Path(__file__).with_name('provision.sh').read_text()
        self.assertIn('70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613', provision)

    def test_all_four_source_argument_combinations(self):
        valid_sha='0'*64
        for image,sha,accepted in ((None,None,True),('base.qcow2',None,False),
                                   (None,valid_sha,False),('base.qcow2',valid_sha,True)):
            with self.subTest(image=image,sha=sha):
                if accepted:supervisor.validate_source_pair(image,sha)
                else:
                    with self.assertRaises(ValueError):supervisor.validate_source_pair(image,sha)

    def test_incomplete_source_pair_has_no_staging_or_download_side_effect(self):
        for image,sha in (('base.qcow2',None),(None,'0'*64)):
            with patch.object(supervisor,'admission') as admission,patch.object(supervisor,'run') as run:
                with self.assertRaises(ValueError):
                    supervisor.base(SimpleNamespace(source_image=image,source_sha256=sha))
                admission.assert_not_called();run.assert_not_called()

    def test_seed_text_format_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'source';path.write_text('trusted')
            self.assertEqual(supervisor.bounded_text(path),'trusted')
            path.write_text('x'*16385)
            with self.assertRaises(ValueError):supervisor.bounded_text(path,16384)
            path.unlink();path.symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):supervisor.bounded_text(path)

    def test_image_names_and_standalone_format(self):
        self.assertEqual(supervisor.image_path('base-cache-v1.qcow2'), supervisor.STATE/'base-cache-v1.qcow2')
        for name in ('../slot-1/job.qcow2', 'slot-1.qcow2', 'base-cache_v1.qcow2'):
            with self.assertRaises(ValueError): supervisor.image_path(name)
        metadata = SimpleNamespace(st_mode=stat.S_IFREG|0o444,st_uid=0,st_gid=0)
        with patch.object(Path,'lstat',return_value=metadata):
            for data in ({'format':'qcow2','virtual-size':120*1024**3},
                         {'format':'qcow2','virtual-size':121*1024**3},
                         {'format':'qcow2','virtual-size':120*1024**3,'backing-filename':'a'},
                         {'format':'qcow2','virtual-size':120*1024**3,'format-specific':{'data':{'data-file':'outside'}}}):
                with patch.object(supervisor,'run',return_value=SimpleNamespace(stdout=json.dumps(data).encode())) as run:
                    if len(data)==2 and data['virtual-size']==120*1024**3:
                        supervisor.verify_image(Path('/unused'))
                    else:
                        with self.assertRaises(RuntimeError): supervisor.verify_image(Path('/unused'))
                    self.assertEqual(run.call_args.kwargs['timeout'],60)
                    self.assertIn('-f',run.call_args.args[0])

    def test_wrong_image_checksum_rejected_before_root_parser(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'base.qcow2';path.write_bytes(b'wrong')
            metadata=SimpleNamespace(st_mode=stat.S_IFREG|0o444,st_uid=0,st_gid=0)
            with patch.object(Path,'lstat',return_value=metadata),patch.object(supervisor,'run') as run:
                with self.assertRaises(RuntimeError):supervisor.verify_image(path,'0'*64)
                run.assert_not_called()

    def test_slots_run_containers_and_the_image_bake_guard_stays(self):
        nix=Path(__file__).parent.parent.joinpath('hound-ci.nix').read_text()
        # The module no longer bakes or boots VM images: slots start job containers.
        self.assertTrue('base.qcow2' not in nix and 'hound-ci-image.service' not in nix and '/dev/kvm' not in nix)
        self.assertTrue('worker --slot ${toString n} --repo ${cfg.repository} --system ${container}' in nix)
        self.assertTrue('Slice = "hound-ci.slice"' in nix)
        code=Path(__file__).with_name('supervisor.py').read_text()
        self.assertLess(code.index('verify_image(disk)'),code.index('disk.rename(final)'))
        self.assertLess(code.index("run(['qemu-img', 'check'"),code.index('disk.rename(final)'))

    def test_default_jit_request_is_the_original_four_label_post(self):
        # Byte-identical to the request before --labels existed.
        self.assertEqual(supervisor.jit_request('xmit-dev/ultimator', 'hound-ci-1-abc', supervisor.DEFAULT_LABELS),
                         ['-X', 'POST', 'repos/xmit-dev/ultimator/actions/runners/generate-jitconfig',
                          '-f', 'name=hound-ci-1-abc', '-F', 'runner_group_id=1',
                          '-f', 'labels[]=self-hosted', '-f', 'labels[]=Linux',
                          '-f', 'labels[]=X64', '-f', 'labels[]=hound-ci', '-f', 'work_folder=_work'])

    def test_jit_request_body_carries_exactly_the_given_labels(self):
        for labels in (['self-hosted', 'Linux', 'X64', 'hound-ci-main'],
                       ['self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-main'],
                       ['hound-ci-main', 'X64', 'Linux', 'self-hosted']):
            with self.subTest(labels=labels):
                request = supervisor.jit_request('xmit-dev/ultimator', 'hound-ci-4-abc', labels)
                fields = [request[i + 1] for i, item in enumerate(request) if item in ('-f', '-F')]
                self.assertEqual([f.removeprefix('labels[]=') for f in fields if f.startswith('labels[]=')], labels)
                self.assertEqual([f for f in fields if not f.startswith('labels[]=')],
                                 ['name=hound-ci-4-abc', 'runner_group_id=1', 'work_folder=_work'])
                self.assertEqual(request[:3], ['-X', 'POST', 'repos/xmit-dev/ultimator/actions/runners/generate-jitconfig'])

    def parse_worker(self, *extra):
        argv = ['hound-ci', 'worker', '--slot', '4', '--repo', 'xmit-dev/ultimator', '--system', SYSTEM,
                '--helper', '/nix/store/x-hound-ci/bin/hound-ci', '--nspawn', '/nix/store/x-systemd/bin/systemd-nspawn',
                '--dataset', 'tank/hound-ci', '--store-paths', PATHS, *extra]
        seen = []
        with patch.object(supervisor.sys, 'argv', argv), patch.object(supervisor, 'worker', side_effect=lambda args: seen.append(args.labels)), \
                patch.object(supervisor, 'STOP_REQUESTED', False), patch.object(supervisor.time, 'sleep', side_effect=SystemExit(0)):
            with self.assertRaises(SystemExit) as stopped:
                supervisor.main()
        return stopped.exception.code, seen

    def test_labels_argument_is_validated_before_any_worker_run(self):
        self.assertEqual(self.parse_worker(), (0, [['self-hosted', 'Linux', 'X64', 'hound-ci']]))
        self.assertEqual(self.parse_worker('--labels', 'self-hosted', 'Linux', 'X64', 'hound-ci-main'),
                         (0, [['self-hosted', 'Linux', 'X64', 'hound-ci-main']]))
        for bad in (['--labels'], ['--labels', 'hound-ci', 'hound-ci'], ['--labels', 'self-hosted', 'gpu'],
                    ['--labels', 'Self-Hosted'], ['--labels', 'hound-ci,hound-ci-main'], ['--labels', '']):
            with self.subTest(bad=bad), patch('sys.stderr'):
                code, seen = self.parse_worker(*bad)
                self.assertEqual((code, seen), (2, []))  # argparse usage error, no worker run
        with self.assertRaisesRegex(ValueError, 'At least one'):
            supervisor.runner_labels([])

    def test_labels_need_the_base_three_and_a_pool_label(self):
        for bad, reason in ((['hound-ci-main'], 'self-hosted, Linux and X64'),
                            (['Linux', 'X64', 'hound-ci'], 'self-hosted, Linux and X64'),
                            (['self-hosted', 'X64', 'hound-ci'], 'self-hosted, Linux and X64'),
                            (['self-hosted', 'Linux', 'hound-ci-main'], 'self-hosted, Linux and X64'),
                            (['self-hosted', 'Linux', 'X64'], 'hound-ci or hound-ci-main')):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, reason):
                    supervisor.runner_labels(bad)
                with self.assertRaisesRegex(ValueError, reason):
                    supervisor.jit_request('xmit-dev/ultimator', 'hound-ci-4-abc', bad)
                with patch('sys.stderr'):
                    self.assertEqual(self.parse_worker('--labels', *bad), (2, []))
        for bad in (['self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-canary'],
                    ['self-hosted', 'Linux', 'X64', 'hound-ci-main', 'hound-ci-canary']):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'canary'):
                supervisor.runner_labels(bad)
        for good in (['self-hosted', 'Linux', 'X64', 'hound-ci-canary'], ['self-hosted', 'Linux', 'X64', 'hound-ci'], ['self-hosted', 'Linux', 'X64', 'hound-ci-main'],
                     ['self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-main']):
            self.assertEqual(supervisor.runner_labels(good), good)

    def test_security_contract(self):
        code = Path(__file__).with_name('supervisor.py').read_text()
        self.assertIn('generate-jitconfig', code)
        self.assertNotIn('/registration-token', code)
        self.assertNotIn('hostfwd=', code)
        self.assertNotIn('guestfwd=', code)
        argv = supervisor.job_unit_argv(2, 'tank/hound-ci', SYSTEM, HELPER, NSPAWN, PATHS)
        nspawn = argv[argv.index(NSPAWN):]
        # Private user and network namespaces; only the closure's store view,
        # read-only; no other host path; no io_uring (Pierre's condition, 10-07).
        for flag in ('--private-users=pick', '--network-veth', '--bind-ro=/run/hound-ci-2/store:/nix/store', '--register=no',
                     '--keep-unit', '--system-call-filter=~io_uring_setup io_uring_enter io_uring_register'):
            self.assertIn(flag, nspawn)
        self.assertEqual([a for a in nspawn if a.startswith(('--bind', '--overlay', '--tmpfs'))],
                         ['--bind-ro=/run/hound-ci-2/store:/nix/store'])
        self.assertEqual([a for a in nspawn if a.startswith('--system-call-filter')],
                         ['--system-call-filter=~io_uring_setup io_uring_enter io_uring_register'])
        for forbidden in ('--capability', '--private-users=no', '--network-zone', '--private-network=no', '--bind-ro=/nix/store',
                          '--bind=', '/var/run/docker.sock', '/nix/var', '--volatile'):
            self.assertFalse([a for a in nspawn if forbidden in a], forbidden)
        start = Path(__file__).with_name('container-start.sh').read_text()
        self.assertIn('--jitconfig', start)
        self.assertNotIn('--no-sandbox', start.replace('(no\n# --no-sandbox)', ''))
        container = Path(__file__).with_name('container.nix').read_text()
        # Runner 2.337.0's internal node (hashFiles) is node20 whatever the environment says:
        # the job runs a runner copy whose externals/node20 is Node 24, never the bare package.
        self.assertIn('ln -s node24 $out/lib/externals/node20', container)
        self.assertIn('hound-ci-start ${runner} ', container)
        self.assertNotIn('hound-ci-start ${pkgs.github-runner}', container)
        self.assertIn('nix.enable = false;', container)
        self.assertIn('boot.isContainer = true;', container)

    def test_job_unit_is_bound_capped_and_named_by_slot(self):
        argv = supervisor.job_unit_argv(3, 'tank/hound-ci', SYSTEM, HELPER, NSPAWN, PATHS)
        self.assertEqual(argv[:5], ['systemd-run', '--quiet', '--wait', '--collect', '--unit=hound-ci-job-3.service'])
        props = dict(a.removeprefix('--property=').split('=', 1) for a in argv if a.startswith('--property='))
        for key, value in {'Slice': 'hound-ci.slice', 'Delegate': 'yes', 'BindsTo': 'hound-ci-3.service',
                           'MemoryMax': '18G', 'MemoryHigh': '17G', 'CPUQuota': '600%', 'RuntimeMaxSec': '8h',
                           'StandardOutput': 'journal', 'Type': 'notify', 'PrivateMounts': 'yes', 'NotifyAccess': 'main',
                           'DevicePolicy': 'closed'}.items():
            self.assertEqual(props[key], value, key)
        pairs = [a.removeprefix('--property=').split('=', 1) for a in argv if a.startswith('--property=')]
        self.assertEqual([v for k, v in pairs if k == 'DeviceAllow'],
                         ['/dev/null rwm', '/dev/zero rwm', '/dev/full rwm', '/dev/random rwm', '/dev/urandom rwm',
                          '/dev/tty rwm', '/dev/ptmx rwm', 'char-pts rw', '/dev/net/tun rwm', '/dev/fuse rwm',
                          '/dev/zfs rw'])
        # Second network layer: no private/link-local/IPv6 destination but loopback and inner Docker.
        self.assertIn('10.0.0.0/8', props['IPAddressDeny'].split())
        self.assertIn('::/0', props['IPAddressDeny'].split())
        self.assertIn('192.168.0.0/16', props['IPAddressDeny'].split())
        self.assertEqual(props['IPAddressAllow'].split(), ['127.0.0.0/8', '::1/128', '172.30.0.0/16', '172.31.255.0/24', '10.231.3.2/32'])
        common = '--slot 3 --dataset tank/hound-ci'
        self.assertEqual(props['ExecStartPre'], f'{HELPER} job-prepare {common}')
        self.assertEqual(props['ExecStartPost'], f'{HELPER} job-network {common}')
        self.assertEqual(props['ExecStopPost'], f'{HELPER} job-cleanup {common}')
        run = argv.index(HELPER)
        self.assertEqual(argv[run:run + 11], [HELPER, 'job-run', '--slot', '3', '--dataset', 'tank/hound-ci',
                                              '--system', SYSTEM, '--store-paths', PATHS, '--'])
        self.assertIn('--directory=/var/lib/hound-ci/job-3/root', argv)
        self.assertIn('--machine=hci-job-3', argv)
        self.assertIn('--load-credential=jit:/run/hound-ci-3/jit', argv)
        self.assertEqual(argv[-1], SYSTEM + '/init')
        for bad in ((0, 'tank/hound-ci', SYSTEM), (6, 'tank/hound-ci', SYSTEM), (1, 'tank', SYSTEM),
                    (1, 'tank/hound-ci', '/nix/store/x/init'), (1, 'tank/hound-ci', '/tmp/nixos-system-hound-ci-1')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                supervisor.job_unit_argv(*bad, HELPER, NSPAWN, PATHS)
        with self.assertRaises(ValueError):
            supervisor.job_unit_argv(1, 'tank/hound-ci', SYSTEM, HELPER, NSPAWN, '/tmp/store-paths')

    def test_job_dataset_never_mounts_in_the_host_namespace_and_busy_ones_go_aside(self):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            if argv[:2] == ['zfs', 'list'] and '-d' in argv:
                return subprocess.CompletedProcess(argv, 0, 'tank/hound-ci\ntank/hound-ci/reap-job-2-x\ntank/hound-ci/job-1\n')
            if argv[:2] == ['zfs', 'list']:
                return subprocess.CompletedProcess(argv, 0)  # the job's dataset exists
            if argv[:3] == ['zfs', 'destroy', '-r'] and argv[3] == 'tank/hound-ci/job-2':
                return subprocess.CompletedProcess(argv, 1)  # busy
            return subprocess.CompletedProcess(argv, 0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(supervisor, 'STATE', Path(tmp)), \
                patch.object(supervisor.subprocess, 'run', side_effect=fake), patch.object(supervisor, 'admission'), \
                patch.object(supervisor, 'message'), patch.object(supervisor, 'require_job_chains') as chains:
            supervisor.job_prepare(SimpleNamespace(slot=2, dataset='tank/hound-ci'))
            chains.assert_called_once()
            create = next(c for c in calls if c[:2] == ['zfs', 'create'])
            self.assertIn('canmount=noauto', create)
            self.assertFalse([c for c in calls if c[:2] == ['zfs', 'mount']])  # never in the host's namespace
            # job-run, in nspawn's own mount namespace: mount, store view, then exec nspawn.
            prepared = list(calls)
            calls.clear()
            Path(tmp, 'job-2').mkdir()  # what `zfs mount` would provide
            order = []
            with patch.object(supervisor, 'store_view', side_effect=lambda *a: order.append(('view', a[1:]))), \
                    patch.object(supervisor.os, 'dup2'), patch.object(supervisor.os, 'execv', side_effect=lambda *a: order.append(('exec', a[0]))):
                supervisor.job_run(SimpleNamespace(slot=2, dataset='tank/hound-ci', system=SYSTEM, store_paths=PATHS,
                                                   command=[NSPAWN, '--quiet']))
            self.assertEqual(calls, [['zfs', 'mount', 'tank/hound-ci/job-2']])
            self.assertEqual(order, [('view', (SYSTEM, PATHS)), ('exec', NSPAWN)])
            self.assertTrue(Path(tmp, 'job-2', 'root', 'usr').is_dir())
        renames = [c for c in prepared if c[:2] == ['zfs', 'rename']]
        self.assertEqual(len(renames), 1)
        self.assertEqual(renames[0][2], 'tank/hound-ci/job-2')
        self.assertTrue(renames[0][3].startswith('tank/hound-ci/reap-job-2-'))
        self.assertIn(['zfs', 'destroy', '-r', 'tank/hound-ci/reap-job-2-x'], prepared)
        self.assertNotIn(['zfs', 'destroy', '-r', 'tank/hound-ci/job-1'], prepared)  # another slot's live job

    def test_job_cleanup_never_fails_on_a_busy_dataset(self):
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1 if argv[:2] == ['zfs', 'destroy'] else 0)
        real_names = supervisor.job_names

        def sandboxed(slot, dataset):
            # Never the live /run/hound-ci-N: job_cleanup removes the JIT file there (on hound, a running job's).
            names = real_names(slot, dataset)
            names.runtime = Path(tmp) / f'run-{slot}'
            names.runtime.mkdir(exist_ok=True)
            names.store = names.runtime / 'store'
            return names
        with tempfile.TemporaryDirectory() as tmp, patch.object(supervisor, 'STATE', Path(tmp)), \
                patch.object(supervisor, 'job_names', sandboxed), \
                patch.object(supervisor.subprocess, 'run', side_effect=fake), patch.object(supervisor, 'message'):
            supervisor.job_cleanup(SimpleNamespace(slot=4, dataset='tank/hound-ci'))
        self.assertTrue(any(c[:3] == ['zfs', 'rename', 'tank/hound-ci/job-4'] and c[3].startswith('tank/hound-ci/reap-job-4-')
                            for c in calls))

    def test_job_prepare_fails_closed_without_the_slots_chains(self):
        names = supervisor.job_names(5, 'tank/hound-ci')
        good = supervisor.job_chains([1, 2, 3, 4, 5], {'10.0.0.0/8'})
        listed = lambda text: patch.object(supervisor, 'run', return_value=SimpleNamespace(stdout=text))
        with listed(good):
            supervisor.require_job_chains(names)
        for text in ('', supervisor.job_chains([1, 2, 3, 4], {'10.0.0.0/8'})):
            with self.subTest(text=text[:20]), listed(text), self.assertRaisesRegex(RuntimeError, 'job chains'):
                supervisor.require_job_chains(names)

    def test_store_view_is_exactly_the_closure_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp, 'host-store')
            store.mkdir()
            system = store / ('s' * 32 + '-nixos-system-hound-ci-1')
            system.mkdir()
            script = store / ('f' * 32 + '-script.sh')
            script.write_text('')
            link = store / ('l' * 32 + '-link')
            link.symlink_to('/nix/store/elsewhere')
            closure = Path(tmp, 'store-paths')
            closure.write_text('\n'.join(map(str, (system, script, link))) + '\n')
            mounts = []
            names = SimpleNamespace(store=Path(tmp, 'run', 'store'))
            Path(tmp, 'run').mkdir()
            with patch.object(supervisor, 'STORE_PATH', __import__('re').compile(r'.*/[a-z0-9]{32}-[A-Za-z0-9+._?=-]+')), \
                    patch.object(supervisor, 'mount', side_effect=lambda *a: mounts.append(a)):
                supervisor.store_view(names, str(system), str(closure))
                self.assertEqual(mounts[0][:3], ('tmpfs', names.store, 'tmpfs'))
                binds = [m for m in mounts if m[0] is not None and m[2] is None]
                self.assertEqual([b[0] for b in binds], [str(system), str(script)])
                for bind in binds:  # each bind remounted read-only, nosuid, nodev
                    remount = mounts[mounts.index(bind) + 1]
                    self.assertEqual((remount[0], remount[1]), (None, bind[1]))
                    self.assertEqual(remount[3] & 0b111, supervisor.MS_RDONLY | supervisor.MS_NOSUID | supervisor.MS_NODEV)
                self.assertEqual(os.readlink(names.store / link.name), '/nix/store/elsewhere')
                self.assertEqual(mounts[-1][3] & supervisor.MS_RDONLY, supervisor.MS_RDONLY)  # the view itself read-only
                closure.write_text(str(script) + '\n')  # the system missing from its own closure
                with self.assertRaisesRegex(RuntimeError, 'closure'):
                    supervisor.store_view(SimpleNamespace(store=Path(tmp, 'other')), str(system), str(closure))

    def test_job_chains_confine_each_veth(self):
        rules = supervisor.job_chains(range(1, 5), {'10.0.0.0/8', '192.168.0.0/16'})
        for n in range(1, 5):
            self.assertIn(f'iifname "ve-hci-job-{n}" ip saddr != 10.231.{n}.2 counter drop', rules)
        veths = '"ve-hci-job-1", "ve-hci-job-2", "ve-hci-job-3", "ve-hci-job-4"'
        self.assertIn(f'iifname {{ {veths} }} counter reject', rules)  # nothing of the host itself
        self.assertIn(f'iifname {{ {veths} }} meta nfproto ipv6 counter reject', rules)
        self.assertIn(f'iifname {{ {veths} }} ip daddr {{ 10.0.0.0/8, 192.168.0.0/16 }} counter reject', rules)
        self.assertIn(f'oifname {{ {veths} }} ct state != {{ established, related }} counter drop', rules)
        self.assertIn(f'ip saddr 10.231.0.0/16 oifname != {{ {veths} }} counter masquerade', rules)
        self.assertNotIn('flush', rules)

    def test_own_unit_cgroup_takes_the_unified_line_only(self):
        base = '/hound.slice/hound-ci.slice/hound-ci-job-2.service'
        self.assertEqual(supervisor.own_unit_cgroup(f'1:net_cls:/\n0::{base}/.control\n'), Path('/sys/fs/cgroup' + base))
        self.assertEqual(supervisor.own_unit_cgroup(f'0::{base}\n'), Path('/sys/fs/cgroup' + base))
        for bad in ('1:net_cls:/\n', f'0::{base}\n0::{base}\n', '0::/system.slice/hound-ci-2.service\n',
                    '0::/hound.slice/hound-ci.slice/hound-ci-job-6.service\n'):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                supervisor.own_unit_cgroup(bad)

    def test_gh_failure_names_the_call_never_its_output(self):
        error = subprocess.CalledProcessError(1, ['gh'], stderr=b'{"token":"ghs_secret"} gh: Server Error (HTTP 502)')
        text = supervisor.gh_failure(['-X', 'POST', 'repos/xmit-dev/ultimator/actions/runners/generate-jitconfig',
                                      '-f', 'name=hound-ci-1-abc'], error)
        self.assertEqual(text, 'gh api failed: POST repos/xmit-dev/ultimator/actions/runners/generate-jitconfig exit=1 http=502')
        listed = supervisor.gh_failure(['repos/x/y/actions/runners?per_page=100', '--paginate'],
                                       subprocess.CalledProcessError(4, ['gh'], stderr=b''))
        self.assertEqual(listed, 'gh api failed: GET repos/x/y/actions/runners exit=4 http=none')

    def test_worker_runs_one_job_unit_and_always_deletes_its_runner(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            system = root / 'system'
            system.mkdir()
            (system / 'init').write_text('')
            runtime = root / 'run'
            runtime.mkdir()
            calls = []
            def fake_gh(arguments):
                calls.append(arguments[:3])
                if 'generate-jitconfig' in ' '.join(arguments):
                    return {'runner': {'id': 77}, 'encoded_jit_config': 'SECRET-JIT'}
                return None
            def fake_subprocess(argv, **kw):
                if argv[0] == 'systemd-run':
                    jit = runtime / 'jit'
                    self.assertEqual((jit.read_text(), jit.stat().st_mode & 0o777), ('SECRET-JIT', 0o600))
                    (runtime / 'console.tail').write_bytes(console)
                    self.assertNotIn('SECRET-JIT', ' '.join(argv))
                return SimpleNamespace(returncode=0 if b'FINISHED' in console else 1)
            names = supervisor.job_names(1, 'tank/hound-ci')
            names.runtime = runtime
            (root / 'store-paths').write_text(str(system) + '\n')
            args = SimpleNamespace(slot=1, repo='xmit-dev/ultimator', system=str(system), helper=HELPER, nspawn=NSPAWN,
                                   dataset='tank/hound-ci', labels=['self-hosted', 'Linux', 'X64', 'hound-ci'],
                                   store_paths=str(root / 'store-paths'))
            # Finished; killed after preflight (OOM, timeout: the job's, not the slot's); never up.
            for console, fails in ((b'HOUND_CI_GUEST_PREFLIGHT_OK\nHOUND_CI_GUEST_JOB_FINISHED status=0\n', False),
                                   (b'HOUND_CI_GUEST_PREFLIGHT_OK\n', False),
                                   (b'HOUND_CI_GUEST_PREFLIGHT_FAILED docker\n', True)):
                calls.clear()
                with self.subTest(fails=fails), patch.object(supervisor, 'STATE', root), \
                        patch.object(supervisor, 'job_names', return_value=names), patch.object(supervisor, 'gh', side_effect=fake_gh), \
                        patch.object(supervisor.subprocess, 'run', side_effect=fake_subprocess), patch.object(supervisor, 'admission'), \
                        patch.object(supervisor, 'message'), patch.object(supervisor, 'job_unit_argv', return_value=['systemd-run']):
                    if fails:
                        with self.assertRaisesRegex(RuntimeError, 'preflight'):
                            supervisor.worker(args)
                    else:
                        supervisor.worker(args)
                    self.assertEqual(calls[-1], ['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/77'])
                    self.assertFalse((runtime / 'jit').exists())
                    self.assertFalse((root / 'slot-1-registration.json').exists())

    def test_worker_stops_a_leftover_job_before_deleting_and_survives_422(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            system = root / 'system'
            system.mkdir()
            (system / 'init').write_text('')
            (root / 'store-paths').write_text(str(system) + '\n')
            runtime = root / 'run'
            runtime.mkdir()
            # A controller restarted mid-job: its record names a runner GitHub still sees busy.
            supervisor.save_record(root / 'slot-1-registration.json', {'repo': 'xmit-dev/ultimator', 'id': 55, 'name': 'hound-ci-1-old'})
            events = []
            busy = subprocess.CalledProcessError(1, ['gh'], stderr=b'HTTP 422')
            def fake_gh(arguments):
                events.append(('gh', ' '.join(arguments[:3])))
                if 'generate-jitconfig' in ' '.join(arguments):
                    return {'runner': {'id': 78}, 'encoded_jit_config': 'J'}
                raise busy  # every DELETE: still busy
            def fake_subprocess(argv, **kw):
                events.append(tuple(argv[:2]))
                if argv[0] == 'systemd-run':
                    (runtime / 'console.tail').write_bytes(b'HOUND_CI_GUEST_PREFLIGHT_OK\n')  # killed mid-job
                return SimpleNamespace(returncode=1)
            names = supervisor.job_names(1, 'tank/hound-ci')
            names.runtime = runtime
            args = SimpleNamespace(slot=1, repo='xmit-dev/ultimator', system=str(system), helper=HELPER, nspawn=NSPAWN,
                                   dataset='tank/hound-ci', labels=['self-hosted', 'Linux', 'X64', 'hound-ci'],
                                   store_paths=str(root / 'store-paths'))
            with patch.object(supervisor, 'STATE', root), patch.object(supervisor, 'job_names', return_value=names), \
                    patch.object(supervisor, 'gh', side_effect=fake_gh), patch.object(supervisor, 'message'), \
                    patch.object(supervisor.subprocess, 'run', side_effect=fake_subprocess), \
                    patch.object(supervisor, 'job_unit_argv', return_value=['systemd-run']):
                supervisor.worker(args)  # no exception: the slot carries on
            stop = events.index(('systemctl', 'stop'))
            first_delete = next(i for i, e in enumerate(events) if e == ('gh', '-X DELETE repos/xmit-dev/ultimator/actions/runners/55'))
            self.assertLess(stop, first_delete)
            self.assertEqual(sorted(p.name for p in root.glob('slot-1-*.json')), ['slot-1-stale-55.json', 'slot-1-stale-78.json'])


if __name__ == '__main__':
    unittest.main()
