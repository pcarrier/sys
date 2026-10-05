#!/usr/bin/env python3
"""Host-free legacy drain tests. No mount, signal, API or credential access."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

gate=module('gate','drain-gh-gate.py');drain=module('drain','drain-old.py')


class DrainTests(unittest.TestCase):
    def test_actual_legacy_post_and_inferred_method_are_blocked(self):
        route=gate.ROUTE
        for args in (['api','-X','POST',route], ['api',route,'--method=POST'],
                     ['api','-XPOST','/'+route], ['api',route,'-F','name=public'],
                     ['api','--input','-',route], ['api',route,'--input=file'],
                     ['api','https://api.github.com/'+route,'--raw-field=name=value']):
            self.assertTrue(gate.blocked(args),args)

    def test_cleanup_other_repos_and_get_delegate(self):
        for args in (['api','-X','DELETE','repos/xmit-dev/ultimator/actions/runners/121'],
                     ['api','repos/xmit-dev/ultimator/actions/runners?per_page=100','--paginate','--slurp'],
                     ['api','-X','POST','repos/other/repo/actions/runners/generate-jitconfig'],
                     ['api',gate.ROUTE], ['api','-X','GET',gate.ROUTE,'-f','x=y'],
                     ['api','repos/xmit-dev/ultimator/other','-f',gate.ROUTE], ['--version'],
                     ['api','--input',gate.ROUTE,'repos/other/repo/endpoint']):
            self.assertFalse(gate.blocked(args),args)

    def test_delegate_preserves_all_arguments_stdin_and_env_except_old_telemetry_default(self):
        args=['api','-X','DELETE','repos/xmit-dev/ultimator/actions/runners/121','--input','-','--header','X-Value: spaced ☃']
        with patch.object(gate.sys,'argv',['gh',*args]),patch.dict(os.environ,{'GH_TELEMETRY':'existing','PUBLIC_TEST':'kept'},clear=True),patch.object(gate.os,'execv') as execute:
            gate.main()
            execute.assert_called_once_with(gate.ORIGINAL,[gate.OLD_GH,*args])
            self.assertEqual(dict(os.environ),{'GH_TELEMETRY':'existing','PUBLIC_TEST':'kept'})
        with patch.object(gate.sys,'argv',['gh','--version']),patch.dict(os.environ,{},clear=True),patch.object(gate.os,'execv'):
            gate.main();self.assertEqual(os.environ['GH_TELEMETRY'],'false')

    def test_block_is_constant_no_argument_or_stdin_leak(self):
        with patch.object(gate.sys,'argv',['gh','api','-X','POST',gate.ROUTE,'-f','name=not-logged']),patch.object(gate.os,'execv') as execute,patch('builtins.print') as printed:
            with self.assertRaises(SystemExit) as stopped:gate.main()
            self.assertEqual(stopped.exception.code,75)
            self.assertEqual(printed.call_args.args,('HOUND_CI_DRAIN_NEW_JIT_BLOCKED',))
            execute.assert_not_called()

    def test_four_namespaces_must_be_distinct_and_not_host(self):
        entries=[{'namespace_inode':i} for i in (2,3,4,5)]
        self.assertTrue(drain.namespaces_valid(entries,1))
        self.assertFalse(drain.namespaces_valid(entries[:3],1))
        self.assertFalse(drain.namespaces_valid(entries,2))
        self.assertFalse(drain.namespaces_valid([{'namespace_inode':2}]*4,1))

    def test_pinned_identity_rejects_exit_pid_reuse_and_service_swap(self):
        entry={'pidfd':123,'nsfd':124,'pid':100,'slot':1,'starttime':'first','namespace_inode':9}
        with patch.object(drain.select,'select',return_value=([123],[],[])):
            with self.assertRaises(RuntimeError):drain.identity(entry)
        with patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain,'starttime',return_value='second'):
            with self.assertRaises(RuntimeError):drain.identity(entry)
        with patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain,'starttime',return_value='first'),patch.object(drain,'properties',return_value={'MainPID':'101'}):
            with self.assertRaises(RuntimeError):drain.identity(entry)

    def test_nsenter_uses_inherited_fd_not_relooked_up_pid(self):
        with patch.object(drain.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            drain.entered({'nsfd':123},['mount','--make-rprivate','/'])
            self.assertIn('--mount=/proc/self/fd/123',run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['pass_fds'],(123,))
            self.assertEqual(run.call_args.kwargs['timeout'],30)

    def test_namespace_gates_private_before_bind_and_positive_control(self):
        commands=[]
        def entered(entry,argv,check=True):
            commands.append(argv)
            if argv[0]=='env':return SimpleNamespace(returncode=75,stderr=b'HOUND_CI_DRAIN_NEW_JIT_BLOCKED\n')
            return SimpleNamespace(stdout=b'gh version 2.101.0 (public)\n')
        with patch.object(drain,'identity'),patch.object(drain,'entered',side_effect=entered):
            drain.gate_namespace({'nsfd':123},Path('/nix/store/reviewed-gate'))
        self.assertEqual(commands[0],['mount','--make-rprivate','/'])
        self.assertEqual(commands[1][:2],['mount','--bind'])
        self.assertIn('remount,bind,ro',commands[2])

    def test_malformed_gate_control_fails_closed(self):
        with patch.object(drain,'identity'),patch.object(drain,'entered',return_value=SimpleNamespace(returncode=0,stderr=b'')):
            with self.assertRaises(RuntimeError):drain.gate_namespace({'nsfd':123},Path('/nix/store/gate'))

    def test_source_order_and_no_signal_restart_credentials_or_payload_reads(self):
        source=Path(__file__).with_name('drain-old.py').read_text()
        self.assertLess(source.index("properties(entry['slot'])['Restart'] != 'no'"),source.index('gate_namespace(entry, gate)\n            manifest'))
        self.assertNotIn('os.kill(',source)
        self.assertNotIn("['systemctl', 'stop'",source)
        self.assertNotIn("['systemctl', 'start'",source)
        self.assertNotIn('/environ',source)
        self.assertNotIn('seed.iso',source)
        self.assertNotIn('hosts.yml',source)
        self.assertNotIn('CREDENTIALS_DIRECTORY',source)


if __name__=='__main__':unittest.main()
