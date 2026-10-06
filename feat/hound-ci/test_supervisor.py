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

    def test_registration_recovery_is_exact_and_preserved_on_api_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'registration.json'
            supervisor.save_record(path, {'repo': 'xmit-dev/ultimator', 'id': 321, 'name': 'hound-ci-1-test'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            failure = subprocess.CalledProcessError(1, ['gh'], stderr=b'HTTP 503')
            with patch.object(supervisor, 'gh', side_effect=failure):
                with self.assertRaises(subprocess.CalledProcessError):
                    supervisor.cleanup_record(path, 'xmit-dev/ultimator')
            self.assertTrue(path.exists())
            with patch.object(supervisor, 'gh', return_value=None) as api:
                supervisor.cleanup_record(path, 'xmit-dev/ultimator')
            api.assert_called_once_with(['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/321'])
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

    def test_current_generation_default_and_candidate_publish_guard(self):
        nix=Path(__file__).parent.parent.joinpath('hound-ci.nix').read_text()
        self.assertIn('default = "base.qcow2"',nix)
        self.assertIn('LimitFSIZE = "32G"',nix)
        self.assertIn('Slice = "hound-ci.slice"',nix)
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
        argv = ['hound-ci', 'worker', '--slot', '4', '--repo', 'xmit-dev/ultimator', '--guest', '/nix/store/x-guest.sh',
                '--image', 'base-cache-v2.qcow2', *extra]
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
        for good in (['self-hosted', 'Linux', 'X64', 'hound-ci'], ['self-hosted', 'Linux', 'X64', 'hound-ci-main'],
                     ['self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-main']):
            self.assertEqual(supervisor.runner_labels(good), good)

    def test_security_contract(self):
        code = Path(__file__).with_name('supervisor.py').read_text()
        self.assertIn('generate-jitconfig', code)
        self.assertNotIn('/registration-token', code)
        self.assertIn("'-enable-kvm'", code)
        self.assertIn("'-netdev', 'user,id=nic,ipv6=off'", code)
        self.assertIn("'--bounding-set=-all'", code)
        self.assertNotIn('hostfwd=', code)
        self.assertNotIn('guestfwd=', code)
        self.assertNotIn("'-virtfs'", code)
        guest = Path(__file__).with_name('guest.sh').read_text()
        self.assertIn('--jitconfig', guest)
        self.assertNotIn('--no-sandbox', guest.replace('# No --no-sandbox:', '# Chrome:'))


if __name__ == '__main__':
    unittest.main()
