#!/usr/bin/env python3
"""Host-free legacy drain tests. No mount, signal, API or credential access."""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack
from types import SimpleNamespace


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

gate=module('gate','drain-gh-gate.py');drain=module('drain','drain-old.py')
INV='c0d966fef80249a1a2bae8ee7e69e38c';NEW_INV='0123456789abcdef0123456789abcdef'
BOOT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


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
        with patch.object(gate.sys,'argv',['gh',*args]),patch.dict(os.environ,{'GH_TELEMETRY':'existing','PUBLIC_TEST':'kept'},clear=True),patch.object(gate.os,'execv') as execute,patch.object(gate.signal,'signal') as disposition:
            gate.main()
            execute.assert_called_once_with(gate.ORIGINAL,[gate.OLD_GH,*args])
            # Ignored SIGPIPE/SIGXFSZ (Python defaults) must not leak into gh.
            self.assertEqual(sorted(call.args for call in disposition.call_args_list),
                             sorted([(gate.signal.SIGPIPE,gate.signal.SIG_DFL),(gate.signal.SIGXFSZ,gate.signal.SIG_DFL)]))
            self.assertEqual(dict(os.environ),{'GH_TELEMETRY':'existing','PUBLIC_TEST':'kept'})
        with patch.object(gate.sys,'argv',['gh','--version']),patch.dict(os.environ,{},clear=True),patch.object(gate.os,'execv'),patch.object(gate.signal,'signal'):
            gate.main();self.assertEqual(os.environ['GH_TELEMETRY'],'false')

    def test_block_is_constant_no_argument_or_stdin_leak(self):
        with patch.object(gate.sys,'argv',['gh','api','-X','POST',gate.ROUTE,'-f','name=not-logged']),patch.object(gate.os,'execv') as execute,patch.object(gate,'blocked_receipt') as receipt,patch('builtins.print') as printed:
            with self.assertRaises(SystemExit) as stopped:gate.main()
            self.assertEqual(stopped.exception.code,75)
            self.assertEqual(printed.call_args.args,('HOUND_CI_DRAIN_NEW_JIT_BLOCKED',))
            execute.assert_not_called()
            receipt.assert_called_once_with()

    def test_four_namespaces_must_be_distinct_and_not_host(self):
        entries=[{'namespace_inode':i} for i in (2,3,4,5)]
        self.assertTrue(drain.namespaces_valid(entries,1))
        self.assertFalse(drain.namespaces_valid(entries[:3],1))
        self.assertFalse(drain.namespaces_valid(entries,2))
        self.assertFalse(drain.namespaces_valid([{'namespace_inode':2}]*4,1))

    def test_pinned_identity_rejects_exit_pid_reuse_and_service_swap(self):
        entry={'pidfd':123,'nsfd':124,'pid':100,'slot':1,'starttime':'first','namespace_inode':9,'invocation_id':INV,'control_group':'/hound.slice/hound-ci.slice/hound-ci-1.service'}
        values={'MainPID':'100','Restart':'no','InvocationID':INV,'ControlGroup':'/hound.slice/hound-ci.slice/hound-ci-1.service'}
        with patch.object(drain,'properties',return_value=values),patch.object(drain.select,'select',return_value=([123],[],[])):
            with self.assertRaisesRegex(RuntimeError,'exited; no replacement'):drain.identity(entry)
        with patch.object(drain,'properties',return_value=values),patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain,'starttime',return_value='second'):
            with self.assertRaisesRegex(RuntimeError,'PID/starttime changed'):drain.identity(entry)
        with patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain,'starttime',return_value='first'),patch.object(drain,'properties',return_value={**values,'MainPID':'101'}):
            with self.assertRaisesRegex(RuntimeError,'PID/starttime changed'):drain.identity(entry)
        with patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain,'starttime',return_value='first'),patch.object(drain.os,'stat',return_value=SimpleNamespace(st_ino=9)),patch.object(drain,'properties',return_value=values):
            self.assertTrue(drain.identity(entry))

    def test_invocation_identity_is_mandatory_and_exact_including_natural_exit(self):
        base={'pidfd':123,'pid':100,'slot':1,'starttime':'first','namespace_inode':9,'control_group':'/hound.slice/hound-ci.slice/hound-ci-1.service'}
        values={'MainPID':'100','Restart':'no','InvocationID':INV,'ControlGroup':base['control_group']}
        for entry,loaded in (({**base},values),({**base,'invocation_id':''},values),({**base,'invocation_id':'C0D966FEF80249A1A2BAE8EE7E69E38C'},{**values,'InvocationID':'C0D966FEF80249A1A2BAE8EE7E69E38C'}),
                             ({**base,'invocation_id':INV},{**values,'InvocationID':NEW_INV}),({**base,'invocation_id':INV},{k:v for k,v in values.items() if k!='InvocationID'})):
            with patch.object(drain,'properties',return_value=loaded),patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain,'starttime',return_value='first'),patch.object(drain.os,'stat',return_value=SimpleNamespace(st_ino=9)):
                with self.subTest(entry=entry.get('invocation_id'),loaded=loaded.get('InvocationID')),self.assertRaisesRegex(RuntimeError,'invocation changed'):drain.identity(entry)
        # A natural exit keeps the dead unit's LAST InvocationID; a restart
        # (even to MainPID 0 again) acquires a new one and is never "natural".
        exited={'MainPID':'0','Restart':'no','InvocationID':NEW_INV}
        with patch.object(drain,'properties',return_value=exited),patch.object(drain.select,'select',return_value=([123],[],[])):
            with self.assertRaisesRegex(RuntimeError,'invocation changed'):drain.identity({**base,'invocation_id':INV},allow_exit=True)

    def test_pin_captures_original_invocation_and_canonical_boot(self):
        values={'MainPID':'100','Restart':'always','InvocationID':INV,'ControlGroup':'/hound.slice/hound-ci.slice/hound-ci-1.service'}
        argv=b'\0'.join([b'python3',drain.OLD_SOURCE.encode(),b'worker',b'--slot',b'1',b'--repo',b'xmit-dev/ultimator',b'--guest',drain.OLD_GUEST.encode(),b'--image',b'base-cache-v2.qcow2'])+b'\0'
        def text(path):
            if path.name=='boot_id':return BOOT+'\n'
            raise AssertionError(path)
        with patch.object(drain,'loaded_wrapper'),patch.object(drain,'properties',return_value=values),patch.object(drain,'starttime',return_value='1024'),patch.object(drain.os,'pidfd_open',return_value=90),patch.object(drain.os,'open',return_value=91),patch.object(drain.os,'fstat',return_value=SimpleNamespace(st_ino=9)),patch.object(drain.os,'stat',return_value=SimpleNamespace(st_ino=9)),patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=901,pw_gid=801)),patch.object(Path,'read_text',text),patch.object(Path,'read_bytes',return_value=argv):
            entry=drain.pin(1)
        self.assertEqual((entry['invocation_id'],entry['boot_id']),(INV,BOOT))
        # Only the loaded cache-v2 argv: the first rollout's (no --image) or a labelled one is refused.
        for other in (argv.replace(b'\0--image\0base-cache-v2.qcow2',b''),argv+b'--labels\0hound-ci\0',argv.replace(b'base-cache-v2',b'base')):
            with self.subTest(argv=other),patch.object(drain,'loaded_wrapper'),patch.object(drain,'properties',return_value=values),patch.object(drain,'starttime',return_value='1024'),patch.object(drain.os,'pidfd_open',return_value=90),patch.object(drain.os,'open',return_value=91),patch.object(drain.os,'close'),patch.object(drain.os,'fstat',return_value=SimpleNamespace(st_ino=9)),patch.object(drain.os,'stat',return_value=SimpleNamespace(st_ino=9)),patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=901,pw_gid=801)),patch.object(Path,'read_text',text),patch.object(Path,'read_bytes',return_value=other):
                with self.assertRaisesRegex(RuntimeError,'legacy supervisor'):drain.pin(1)
        for bad in ({**values,'InvocationID':''},{**values,'InvocationID':'x'*32},{k:v for k,v in values.items() if k!='InvocationID'}):
            with patch.object(drain,'loaded_wrapper'),patch.object(drain,'properties',return_value=bad),patch.object(drain.os,'pidfd_open') as opened:
                with self.assertRaisesRegex(RuntimeError,'invocation identity missing'):drain.pin(1)
                opened.assert_not_called()
        for boot in ('AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA','aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa','not-a-uuid'):
            with patch.object(Path,'read_text',return_value=boot+'\n'),self.assertRaisesRegex(RuntimeError,'Canonical kernel boot identity'):
                drain.current_boot_id()

    def test_generation_pins_the_loaded_cache_v2_controllers(self):
        self.assertEqual((drain.OLD_SOURCE, drain.OLD_SOURCE_SHA, drain.OLD_GUEST, drain.OLD_WRAPPER, drain.OLD_WRAPPER_SHA, drain.OLD_IMAGE_ARGS),
                         ('/nix/store/snp22ndcxkxigcyhlxzm5rp8fpw5j19f-supervisor.py',
                          'ea34b0dd3a01529a8ebc9aeab4426f7068ee69927092454da28e19632a88863c',
                          '/nix/store/sawmqyv0izn8ck3pbq2q0gbg770h2d4i-guest.sh',
                          '/nix/store/grszl3cvcvy5wxxwgjwr1zydpi2ivk0h-hound-ci/bin/hound-ci',
                          '908bb2383a8e5ebb428ae87f9be8910b319619da9e64778ed369b939f1f47a16', ['--image', 'base-cache-v2.qcow2']))
        self.assertEqual((str(drain.STATE), drain.WITNESS_SINCE), ('/var/lib/hound-ci/rollout-main-slot-20261006', '2026-10-06T11:45:00+00:00'))

    def test_old_wrapper_PATH_resolves_gh_to_the_gated_directory(self):
        import hashlib
        python='/nix/store/p-python3-3.14.7/bin';curl='/nix/store/c-curl/bin';later='/nix/store/z-later/bin'
        def wrapper(dirs):
            return ('#!/bin/bash\nset -o errexit\nexport PATH="'+':'.join(dirs)+':$PATH"\n\nexec python3 '+drain.OLD_SOURCE+' "$@"\n').encode()
        good=wrapper([python,curl,str(drain.GH.parent),later])
        def check(data,shadow=()):
            with patch.object(drain,'OLD_WRAPPER_SHA',hashlib.sha256(data).hexdigest()),patch.object(Path,'read_bytes',return_value=data),patch.object(drain.os.path,'lexists',side_effect=lambda path:path in shadow):
                drain.wrapper_resolves_gate()
        check(good)
        check(good,shadow=(later+'/gh',))  # After the gated dir: never consulted first.
        for data,shadow in ((good,(curl+'/gh',)),(wrapper([python,curl,later]),()),
                            (wrapper([curl,str(drain.GH.parent)]),()),(good.replace(b'exec python3',b'exec env python3'),()),
                            (wrapper(['/usr/bin',str(drain.GH.parent)]),())):
            with self.subTest(shadow=shadow),self.assertRaises(RuntimeError):check(data,shadow)
        with patch.object(Path,'read_bytes',return_value=good),self.assertRaisesRegex(RuntimeError,'reviewed pin'):
            drain.wrapper_resolves_gate()
        prefix='{ path='+drain.OLD_WRAPPER+' ; argv[]='+drain.OLD_WRAPPER+' worker --slot 2 --repo xmit-dev/ultimator ; }\n'
        with patch.object(drain,'run',return_value=SimpleNamespace(stdout=prefix)):
            drain.loaded_wrapper(2)
            with self.assertRaisesRegex(RuntimeError,'old wrapper'):drain.loaded_wrapper(1)
        with patch.object(drain,'run',return_value=SimpleNamespace(stdout=prefix.replace('grszl3cv','xrszl3cv'))),self.assertRaisesRegex(RuntimeError,'old wrapper'):
            drain.loaded_wrapper(2)

    def test_gate_reads_manifest_beyond_64KiB_up_to_bound(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'manifest.json'
            value={'drain_witness':{'1':{'vm_history':[{'name':'hound-ci-1-%012x'%i,'start_monotonic':str(i)} for i in range(4096)]}}}
            path.write_text(json.dumps(value,indent=2));path.chmod(0o600)
            self.assertGreater(path.stat().st_size,65536)
            real=os.fstat
            owner=lambda fd:SimpleNamespace(**{key:getattr(real(fd),key) for key in ('st_mode','st_size')},st_uid=0)
            with patch.object(gate.os,'fstat',side_effect=owner):
                self.assertEqual(gate.read_root_json(path,gate.MANIFEST_LIMIT),value)
                with self.assertRaises(RuntimeError):gate.read_root_json(path,65536)
            self.assertEqual(gate.MANIFEST_LIMIT,16*1024*1024)

    def test_root_entry_points_require_pinned_interpreter(self):
        with patch.object(drain.sys,'flags',SimpleNamespace(isolated=0,dont_write_bytecode=1)),self.assertRaisesRegex(RuntimeError,'pinned Nix Python'):
            drain.require_pinned_interpreter()
        with patch.object(drain.sys,'flags',SimpleNamespace(isolated=1,dont_write_bytecode=1)),patch.object(drain.sys,'executable','/usr/bin/python3'),self.assertRaisesRegex(RuntimeError,'pinned Nix Python'):
            drain.require_pinned_interpreter()
        with patch.object(drain.sys,'flags',SimpleNamespace(isolated=1,dont_write_bytecode=1)),patch.object(drain.sys,'executable',drain.PINNED_PYTHON):
            drain.require_pinned_interpreter()

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

    def test_nonexecutable_or_noncanonical_gate_fails_before_mount(self):
        for mode in (0o100444,0o100644,0o100755):
            with patch.object(Path,'resolve',return_value=Path('/nix/store/test-gate')),patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=mode,st_uid=0)):
                with self.assertRaises(RuntimeError):drain.validated_gate(Path('/nix/store/test-gate'),'unused')
        with self.assertRaises(RuntimeError):drain.validated_gate(Path('/nix/store/../outside'),'unused')

    def test_partial_bind_intent_survives_remount_failure(self):
        stages=[]
        def entered(entry,argv,check=True):
            if 'remount,bind,ro' in argv:raise RuntimeError('measured synthetic remount failure')
            return SimpleNamespace(stdout=b'')
        with patch.object(drain,'identity'),patch.object(drain,'entered',side_effect=entered):
            with self.assertRaises(RuntimeError):drain.gate_namespace({},Path('/nix/store/gate'),stages.append)
        self.assertEqual(stages,['private-intent','private','bind-intent','bound','readonly-intent'])

    def test_postgate_natural_exit_not_pid_replacement(self):
        entry={'pidfd':123,'pid':100,'slot':1,'invocation_id':INV}
        with patch.object(drain.select,'select',return_value=([123],[],[])),patch.object(drain,'properties',return_value={'MainPID':'0','Restart':'no','InvocationID':INV}):
            self.assertFalse(drain.identity(entry,allow_exit=True))
        for values in ({'MainPID':'101','Restart':'no','InvocationID':INV},{'MainPID':'0','Restart':'always','InvocationID':INV}):
            with patch.object(drain.select,'select',return_value=([123],[],[])),patch.object(drain,'properties',return_value=values):
                with self.assertRaises(RuntimeError):drain.identity(entry,allow_exit=True)

    def test_loaded_hold_or_cgroup_drift_fails(self):
        entry={'pidfd':123,'pid':100,'slot':1,'invocation_id':INV,'control_group':'/hound.slice/hound-ci.slice/hound-ci-1.service'}
        for values in ({'MainPID':'100','Restart':'always','InvocationID':INV,'ControlGroup':entry['control_group']},
                       {'MainPID':'100','Restart':'no','InvocationID':INV,'ControlGroup':'/unexpected'}):
            with patch.object(drain,'properties',return_value=values),patch.object(drain.select,'select',return_value=([],[],[])):
                with self.assertRaises(RuntimeError):drain.identity(entry,require_hold=True)

    def test_manifest_is_file_and_directory_fsynced(self):
        with tempfile.TemporaryDirectory() as root,patch.object(drain,'STATE',Path(root)),patch.object(drain.os,'fsync',wraps=drain.os.fsync) as sync:
            drain.save({'phase':'bind-intent'})
            self.assertEqual(sync.call_count,2)
            self.assertEqual((Path(root)/'manifest.json').stat().st_mode&0o777,0o600)

    def test_dropin_write_intent_precedes_file_and_records_partial_failure(self):
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'slot.service.d'/drain.DROPIN
            manifest={'dropins':[]};receipts=[]
            def saved(value):
                receipts.append((value['dropins'][-1]['stage'],target.exists()))
            with patch.object(drain,'fixed_dropin_paths',return_value=[target]*4),patch.object(drain,'save',side_effect=saved):
                drain.write_hold({'slot':1},manifest)
            self.assertEqual(receipts,[('write-intent',False),('written-not-yet-loaded',True)])
            self.assertEqual(manifest['dropins'][0]['path'],str(target))
            self.assertEqual(target.read_text(),'[Service]\nRestart=no\n')
            interrupted={'dropins':[]}
            with patch.object(drain,'fixed_dropin_paths',return_value=[target]*4),patch.object(drain,'save'):
                with self.assertRaises(RuntimeError):drain.write_hold({'slot':1},interrupted)
            self.assertEqual(interrupted['dropins'][0]['stage'],'write-intent')

    def test_recovery_fixed_paths_cover_all_four_without_manifest(self):
        self.assertEqual([str(path) for path in drain.fixed_dropin_paths()],
                         [f'/run/systemd/system/hound-ci-{slot}.service.d/{drain.DROPIN}' for slot in range(1,5)])

    def test_actual_qemu_slot_disk_parent_and_isolation_binding(self):
        entry={'slot':1,'pid':100};account=SimpleNamespace(pw_uid=901,pw_gid=801)
        fields={'PPid':'100','Uid':'901 901 901 901','Gid':'801 801 801 801','NoNewPrivs':'1','Seccomp':'2',
                **{key:'00000000' for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')}}
        args=['qemu-system-x86_64','-drive','file=/var/lib/hound-ci/slot-1/job.qcow2,if=virtio,format=qcow2,cache=none,discard=unmap']
        result=drain.validate_qemu_binding(entry,110,'1024',fields,args,account)
        self.assertTrue(result['isolation_verified']);self.assertEqual(result['parent_pid'],100)
        for key,bad in (('PPid','101'),('Uid','902 902 902 902'),('CapEff','1'),('NoNewPrivs','0'),('Seccomp','0')):
            with self.assertRaises(RuntimeError):drain.validate_qemu_binding(entry,110,'1024',{**fields,key:bad},args,account)
        with self.assertRaises(RuntimeError):drain.validate_qemu_binding(entry,110,'1024',fields,['qemu','-drive','file=/other/slot/job.qcow2'],account)

    def test_delete_receipt_exact_runner_identity_and_truthful_api_outcome(self):
        entry={'slot':1,'pid':100,'starttime':'1024'}
        caller=({'drain_nonce':'a'*32,'gate_sha256':'b'*64},entry,{'repo':'xmit-dev/ultimator','id':121,'name':'hound-ci-1-abcdef012345'})
        self.assertEqual(gate.exact_cleanup(['api','-X','DELETE','repos/xmit-dev/ultimator/actions/runners/121']),121)
        self.assertIsNone(gate.exact_cleanup(['api','-X','DELETE','repos/other/repo/actions/runners/121']))
        for returncode,stderr,success in ((0,b'',True),(1,b'HTTP 404',True),(1,b'HTTP 403',False),(-9,b'',False),(-13,b'HTTP 404',False)):
            saved=[]
            def save(name,value):saved.append((name,dict(value)))
            with patch.object(gate,'pinned_caller',return_value=caller),patch.object(gate,'save_receipt',side_effect=save),patch.object(gate.subprocess,'run',return_value=SimpleNamespace(returncode=returncode,stderr=stderr)),patch.object(gate.sys,'stderr'):
                with self.assertRaises(SystemExit) as stopped:gate.delegated_cleanup(['api','-X','DELETE','repos/xmit-dev/ultimator/actions/runners/121'],121)
            # Signal death maps to 128+N (0..255, as the finisher requires) and is never success.
            expected=returncode if returncode>=0 else 128-returncode
            self.assertEqual((stopped.exception.code,saved[-1][1]['returncode']),(expected,expected))
            self.assertEqual([value['stage'] for _,value in saved],['delete-intent','delete-returned'])
            self.assertEqual(saved[-1][1]['success'],success)
            self.assertEqual(saved[-1][1]['name'],caller[2]['name'])
        with patch.object(gate,'pinned_caller',return_value=caller),patch.object(gate,'save_receipt') as save:
            with self.assertRaises(RuntimeError):gate.delegated_cleanup([],122)
            save.assert_not_called()

    def test_qemu_orchestration_brackets_every_field_and_checks_real_executable(self):
        entry={'slot':1,'pid':100,'boot_id':BOOT};registration={'repo':'xmit-dev/ultimator','id':121,'name':'hound-ci-1-abcdef012345'}
        account=SimpleNamespace(pw_uid=901,pw_gid=801)
        fields={'Name':'qemu-system-x86_64','PPid':'100','Uid':'901 901 901 901','Gid':'801 801 801 801','NoNewPrivs':'1','Seccomp':'2',
                **{key:'00000000' for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')}}
        raw=b'qemu\0-drive\0file=/var/lib/hound-ci/slot-1/job.qcow2,if=virtio,format=qcow2,cache=none,discard=unmap\0'
        events=[]
        def text(path):
            if path.name=='children':return '110'
            if path.name=='boot_id':return 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
            events.append('status');return '\n'.join(f'{key}: {value}' for key,value in fields.items())
        def clock(pid):events.append('start');return '1024'
        def opened(pid):events.append('pidfd');return 90
        with patch.object(drain,'identity'),patch.object(drain,'public_registration',return_value=registration),patch.object(Path,'read_text',text),patch.object(Path,'read_bytes',return_value=raw),patch.object(drain,'starttime',side_effect=clock),patch.object(drain.os,'pidfd_open',side_effect=opened),patch.object(drain.os,'close'),patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain.pwd,'getpwnam',return_value=account),patch.object(drain.os,'readlink',return_value=drain.QEMU_ELF):
            self.assertTrue(drain.current_qemu(entry)['qemu']['isolation_verified'])
        self.assertEqual(events[:3],['start','pidfd','status'])
        self.assertEqual(events[-1],'start')
        for clocks,executable,rejected in ((['100','200'],drain.QEMU_ELF,False),(['100','100'],'/untrusted/qemu',True)):
            with patch.object(drain,'identity'),patch.object(drain,'public_registration',return_value=registration),patch.object(Path,'read_text',text),patch.object(Path,'read_bytes',return_value=raw),patch.object(drain,'starttime',side_effect=clocks),patch.object(drain.os,'pidfd_open',return_value=90),patch.object(drain.os,'close'),patch.object(drain.select,'select',return_value=([],[],[])),patch.object(drain.pwd,'getpwnam',return_value=account),patch.object(drain.os,'readlink',return_value=executable):
                if rejected:
                    with self.assertRaises(RuntimeError):drain.current_qemu(entry)
                else:self.assertIsNone(drain.current_qemu(entry)['qemu'])

    def test_delete_receipt_persistence_failure_is_fail_closed_not_success(self):
        caller=({'drain_nonce':'a'*32,'gate_sha256':'b'*64},{'slot':1,'pid':100,'starttime':'1024'},
                {'repo':'xmit-dev/ultimator','id':121,'name':'hound-ci-1-abcdef012345'})
        for failures,calls in (([OSError('synthetic persistence failure')],0),([None,OSError('synthetic after-return failure')],1)):
            with patch.object(gate,'pinned_caller',return_value=caller),patch.object(gate,'save_receipt',side_effect=failures),patch.object(gate.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr=b'')) as run,patch.object(gate.sys,'stderr'):
                with self.assertRaises(OSError):gate.delegated_cleanup(['api','-X','DELETE','repos/xmit-dev/ultimator/actions/runners/121'],121)
                self.assertEqual(run.call_count,calls)

    def test_first_registration_boundary_is_real_monotonic_and_precedes_read(self):
        events=[]
        def text(path):
            if path.name=='boot_id':return 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
            if path.name=='children':return ''
            raise AssertionError('Unexpected public path')
        def mono():events.append('monotonic');return 123456789000
        def record(slot):events.append('registration');return None
        def wall():events.append('utc');return '2026-10-05T17:00:00+00:00'
        with patch.object(drain,'identity'),patch.object(Path,'read_text',text),patch.object(drain.time,'monotonic_ns',side_effect=mono),patch.object(drain,'timestamp',side_effect=wall),patch.object(drain,'public_registration',side_effect=record):
            result=drain.current_qemu({'pid':100,'slot':1,'boot_id':BOOT})
        self.assertEqual(events[:3],['monotonic','utc','registration'])
        self.assertEqual(result['observation_started_monotonic_us'],'123456789')
        self.assertEqual(result['observation_boot_id'],'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
        self.assertIsNone(result['registration'])
        self.assertEqual(result['first_registration_read'],{'path':'/var/lib/hound-ci/slot-1-registration.json','present':False,
                                                           'boot_id':BOOT,'after_monotonic_us':'123456789'})

    def test_first_read_presence_is_source_bound_verbatim_and_later_reads_never_substitute(self):
        reads=iter([{'repo':'xmit-dev/ultimator','id':None,'name':'hound-ci-1-abcdef012345'},None])
        def text(path):return BOOT if path.name=='boot_id' else ''
        with patch.object(drain,'identity'),patch.object(Path,'read_text',text),patch.object(drain.time,'monotonic_ns',return_value=5000),patch.object(drain,'public_registration',side_effect=lambda slot:next(reads)):
            result=drain.current_qemu({'pid':100,'slot':1,'boot_id':BOOT})
        # Record PRESENT at the first read, absent by the second: still present.
        self.assertTrue(result['first_registration_read']['present'])
        self.assertEqual(result['registration']['name'],'hound-ci-1-abcdef012345')

    def test_observation_on_another_boot_than_pin_is_rejected_before_any_registration_read(self):
        def text(path):return 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' if path.name=='boot_id' else ''
        with patch.object(drain,'identity'),patch.object(Path,'read_text',text),patch.object(drain,'public_registration') as record:
            with self.assertRaisesRegex(RuntimeError,'another boot'):drain.current_qemu({'pid':100,'slot':1,'boot_id':BOOT})
            record.assert_not_called()

    def test_wallclock_jump_does_not_relabel_first_read_monotonic_boundary(self):
        def text(path):return 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' if path.name=='boot_id' else ''
        with patch.object(drain,'identity'),patch.object(Path,'read_text',text),patch.object(drain.time,'monotonic_ns',return_value=123000),patch.object(drain,'timestamp',side_effect=['2026-10-05T17:00:00+00:00','2026-10-05T16:00:00+00:00']),patch.object(drain,'public_registration',return_value=None):
            result=drain.current_qemu({'pid':100,'slot':1,'boot_id':BOOT})
        self.assertEqual(result['observation_started_monotonic_us'],'123')
        self.assertEqual(result['first_registration_read']['after_monotonic_us'],'123')
        self.assertGreater(result['observation_started_utc'],result['utc'])

    # ---- Pre-arm readiness: idle runners, history caps (read-only, pre-mutation)
    REG={slot:{'repo':'xmit-dev/ultimator','id':100+slot,'name':f'hound-ci-{slot}-00000000000{slot}'} for slot in range(1,5)}
    PROPS={slot:{'MainPID':str(1000+slot),'Restart':'always','InvocationID':f'{slot:x}'*32,'ControlGroup':f'/hound.slice/hound-ci.slice/hound-ci-{slot}.service'} for slot in range(1,5)}

    def runners(self,**changes):
        rows=[{'id':100+slot,'name':f'hound-ci-{slot}-00000000000{slot}','status':'online','busy':True,'os':'Linux','labels':[]} for slot in range(1,5)]
        rows.append({'id':7,'name':'other-runner','status':'offline','busy':False})
        for slot,change in changes.items():rows[int(slot[1:])-1].update(change)
        return rows

    def readiness(self,stack,rows=None,registrations=None,starts=None,props=None):
        registrations=registrations or (lambda slot:self.REG[slot])
        stack.enter_context(patch.object(drain,'properties',side_effect=props or (lambda slot:self.PROPS[slot])))
        stack.enter_context(patch.object(drain,'public_registration',side_effect=registrations))
        counts=stack.enter_context(patch.object(drain,'vm_starts',side_effect=lambda slot,pid,inv:(starts or {}).get(slot,75)))
        listing=stack.enter_context(patch.object(drain,'runner_liveness',return_value=self.runners() if rows is None else rows))
        return counts,listing

    def test_readiness_requires_all_four_listed_online_busy_with_the_exact_registration(self):
        with ExitStack() as stack:
            counts,listing=self.readiness(stack)
            ready=drain.readiness()
        listing.assert_called_once_with()
        self.assertEqual(ready['registrations'],{str(k):v for k,v in self.REG.items()})
        self.assertEqual(ready['vm_starts'],{'1':75,'2':75,'3':75,'4':75})
        self.assertEqual(ready['controllers']['2'],{'pid':1002,'invocation_id':'2'*32})
        self.assertEqual(ready['liveness_source'],'uid1000-ordinary-gh-config')
        for rows,message in ((self.runners(s2={'busy':False}),'slot 2 runner is online/idle'),
                             (self.runners(s3={'status':'offline'}),'slot 3 runner is offline/busy'),
                             (self.runners(s4={'name':'hound-ci-4-ffffffffffff'}),'slot 4 registered runner is not listed'),
                             ([row for row in self.runners() if row['id']!=101],'slot 1 registered runner is not listed')):
            with ExitStack() as stack:
                self.readiness(stack,rows=rows)
                with self.subTest(message=message),self.assertRaisesRegex(drain.NotReady,message):drain.readiness()
        for missing in (None,{**self.REG[3],'id':None}):
            with ExitStack() as stack:
                _,listing=self.readiness(stack,registrations=lambda slot:missing if slot==3 else self.REG[slot])
                with self.assertRaisesRegex(drain.NotReady,'slot 3 has no positive runner registration'):drain.readiness()
                listing.assert_not_called()

    def test_readiness_rereads_registration_and_controller_after_the_GET(self):
        reads={slot:0 for slot in range(1,5)}
        def registration(slot):
            reads[slot]+=1
            return self.REG[slot] if slot!=2 or reads[slot]==1 else None  # R ended during the GET
        with ExitStack() as stack:
            self.readiness(stack,registrations=registration)
            with self.assertRaisesRegex(drain.NotReady,'slot 2 changed during the readiness check'):drain.readiness()
        calls={slot:0 for slot in range(1,5)}
        def props(slot):
            calls[slot]+=1
            return self.PROPS[slot] if slot!=4 or calls[slot]==1 else {**self.PROPS[4],'InvocationID':'f'*32}
        with ExitStack() as stack:
            self.readiness(stack,props=props)
            with self.assertRaisesRegex(drain.NotReady,'slot 4 changed'):drain.readiness()

    def test_history_half_caps_are_pinned_to_the_waiter_and_checked_before_the_GET(self):
        waiter=module('waiter_caps','wait-drained.py')
        self.assertEqual((drain.WAITER_SLOT_HISTORY,drain.WAITER_ALL_HISTORY),(waiter.MAX_VM_HISTORY,waiter.MAX_ALL_VM_HISTORY))
        self.assertEqual((drain.PREARM_SLOT_STARTS,drain.PREARM_ALL_STARTS),(1024,2048))
        for starts,message in (({1:1025},'slot 1 VM history 1025'),({1:1024,2:1024,3:1},'All-slot VM history')):
            with ExitStack() as stack:
                _,listing=self.readiness(stack,starts=starts)
                with self.subTest(starts=starts),self.assertRaisesRegex(RuntimeError,message):drain.readiness()
                listing.assert_not_called()
        with ExitStack() as stack:
            self.readiness(stack,starts={1:1024,2:1024,3:0,4:0})
            drain.readiness()

    def test_vm_starts_counts_only_the_pinned_invocation_pid_and_slot_root_STARTs(self):
        inv='1'*32
        def row(message,**changes):
            value={'MESSAGE':message,'_PID':'1001','_UID':'0','_SYSTEMD_UNIT':'hound-ci-1.service','_SYSTEMD_INVOCATION_ID':inv,'__CURSOR':'c'}
            value.update(changes);return json.dumps(value).encode()
        start='HOUND_CI slot=1 name=hound-ci-1-0123456789ab repo=xmit-dev/ultimator disposable VM started'
        lines=[row(start),row(start.replace(' repo=xmit-dev/ultimator','')),row(start,_PID='1002'),row(start,_UID='1000'),
               row(start,_SYSTEMD_INVOCATION_ID='2'*32),row(start,_SYSTEMD_UNIT='hound-ci-2.service'),
               row(start.replace('slot=1','slot=2')),row('HOUND_CI slot=1 VM stopped; preflight=True; runner completed=True; erasing disk'),
               row(start+' extra'),row(start.replace('repo=xmit-dev/ultimator','repo=o/r')),json.dumps({'MESSAGE':[1,2],'__CURSOR':'c'}).encode()]
        with patch.object(drain,'bounded_child',return_value=b'\n'.join(lines)+b'\n') as child:
            self.assertEqual(drain.vm_starts(1,1001,inv),2)
        argv=child.call_args.args[0]
        self.assertEqual(argv[:6],['journalctl','--boot','--utc','--no-pager','--no-tail','--output=json'])
        self.assertEqual(argv[-1],'_SYSTEMD_INVOCATION_ID='+inv)
        self.assertEqual(child.call_args.args[1:],(drain.JOURNAL_LIMIT,drain.JOURNAL_TIMEOUT))
        with patch.object(drain,'bounded_child',return_value=b'{"x":"'+b'a'*drain.MAX_ROW+b'"}\n'),self.assertRaisesRegex(RuntimeError,'row bound'):
            drain.vm_starts(1,1001,inv)
        for pid,invocation in ((1,inv),(1001,'x'*32)):
            with patch.object(drain,'bounded_child') as child,self.assertRaisesRegex(RuntimeError,'identity unavailable'):
                drain.vm_starts(1,pid,invocation)
            child.assert_not_called()

    def test_runner_liveness_is_one_uid1000_GET_complete_in_one_page(self):
        listing={'total_count':5,'runners':self.runners()}
        with patch.object(drain,'bounded_child',return_value=json.dumps(listing).encode()) as child:
            rows=drain.runner_liveness()
        self.assertEqual(rows[0],{'id':101,'name':'hound-ci-1-000000000001','status':'online','busy':True})
        argv=child.call_args.args[0]
        self.assertEqual(argv[:4],[drain.SETPRIV,'--reuid=1000','--regid=100','--clear-groups'])
        self.assertIn('--no-new-privs',argv)
        self.assertEqual(argv[-7:],[str(drain.GH_ELF),'api','--hostname','github.com','--method','GET',drain.RUNNERS_ROUTE])
        self.assertEqual(child.call_args.kwargs['env']['HOME'],'/home/pcarrier')
        self.assertEqual(child.call_args.kwargs['cwd'],'/var/empty')
        self.assertEqual(child.call_args.args[1:],(drain.LIVENESS_LIMIT,drain.LIVENESS_TIMEOUT))
        for bad in ({'total_count':101,'runners':[]},{'total_count':6,'runners':self.runners()},{'runners':[]},[],
                    {'total_count':1,'runners':[{'id':1,'name':'a','status':'online','busy':'true'}]},
                    {'total_count':2,'runners':[{'id':1,'name':'a','status':'online','busy':True}]*2}):
            with patch.object(drain,'bounded_child',return_value=json.dumps(bad).encode()),self.subTest(bad=bad),self.assertRaises(RuntimeError):
                drain.runner_liveness()

    def test_bounded_child_kills_only_its_own_child_on_oversize_timeout_or_failure(self):
        python=sys.executable
        self.assertEqual(drain.bounded_child([python,'-c','print("ok")'],100,10),b'ok\n')
        with self.assertRaisesRegex(RuntimeError,'oversized'):drain.bounded_child([python,'-c','print("x"*200)'],100,10)
        with self.assertRaisesRegex(RuntimeError,'timeout'):drain.bounded_child([python,'-c','import time; time.sleep(30)'],100,0.3)
        with self.assertRaisesRegex(RuntimeError,'failed'):drain.bounded_child([python,'-c','raise SystemExit(3)'],100,10)
        killed=[]
        real=drain.subprocess.Popen
        def spawn(*args,**kwargs):
            child=real(*args,**kwargs);killed.append(child);return child
        with patch.object(drain.subprocess,'Popen',side_effect=spawn),self.assertRaisesRegex(RuntimeError,'timeout'):
            drain.bounded_child([python,'-c','import time; time.sleep(30)'],100,0.3)
        self.assertIsNotNone(killed[0].returncode)  # killed and reaped, no orphan

    def test_idle_risk_flags_new_registrations_and_slow_binds_never_refuses(self):
        R=self.REG[1];new={'repo':'xmit-dev/ultimator','id':None,'name':'hound-ci-1-ffffffffffff'}
        self.assertEqual(drain.idle_risk(R,R,0,9999999,R),[])
        self.assertEqual(drain.idle_risk(R,None,0,9999999,None),[])
        self.assertEqual(drain.idle_risk(R,R,0,10000000,R),['bind-later-than-legacy-sleep'])
        self.assertEqual(drain.idle_risk(R,new,0,1,new),['new-registration-before-bind'])
        self.assertEqual(drain.idle_risk(R,None,0,1,new),['new-registration-after-gate'])
        self.assertEqual(drain.idle_risk(R,R,0,1,{**new,'id':5}),['new-registration-after-gate'])

    def arm_fixture(self,stack,folder,ready=None,registrations=None):
        state=Path(folder)/'state'
        stack.enter_context(patch.object(drain,'STATE',state))
        stack.enter_context(patch.object(drain,'fixed_dropin_paths',return_value=[Path(folder)/f'd{slot}'/'x.conf' for slot in range(1,5)]))
        for name in ('validated_gate','reviewed_source','identity','run'):
            stack.enter_context(patch.object(drain,name))
        stack.enter_context(patch.object(drain,'write_hold',side_effect=lambda entry,manifest:manifest['dropins'].append({'slot':entry['slot'],'stage':'written-not-yet-loaded'})))
        stack.enter_context(patch.object(drain.os,'geteuid',return_value=0))
        stack.enter_context(patch.object(drain,'digest',return_value='d'*64))
        stack.enter_context(patch.object(drain,'current_boot_id',return_value=BOOT))
        stack.enter_context(patch.object(drain,'GH_ELF',SimpleNamespace(open=lambda mode:io.BytesIO(b'\x7fELF'))))
        real_stat=os.stat
        stack.enter_context(patch.object(drain.os,'stat',side_effect=lambda path,*a,**k:SimpleNamespace(st_ino=1) if str(path).endswith('/ns/mnt') else real_stat(path,*a,**k)))
        stack.enter_context(patch.object(drain,'properties',side_effect=lambda slot:{**self.PROPS[slot],'Restart':'no'}))
        readiness=stack.enter_context(patch.object(drain,'readiness',side_effect=ready or (lambda:{'controllers':{str(s):{'pid':1000+s,'invocation_id':f'{s:x}'*32} for s in range(1,5)},'registrations':{str(k):v for k,v in self.REG.items()}})))
        stack.enter_context(patch.object(drain,'pin',side_effect=lambda slot:{'slot':slot,'pid':1000+slot,'invocation_id':f'{slot:x}'*32,'boot_id':BOOT,'namespace_inode':10+slot,'pidfd':-1,'nsfd':-1}))
        stack.enter_context(patch.object(drain,'namespaces_valid',return_value=True))
        stack.enter_context(patch.object(drain.os,'close'))
        stack.enter_context(patch.object(drain,'current_qemu',side_effect=lambda entry:{'registration':self.REG[entry['slot']],'qemu':None,'utc':'u','observation_boot_id':BOOT,
            'observation_started_monotonic_us':'1','observation_started_utc':'u','first_registration_read':{'present':True}}))
        def gate(entry,gate_path,receipt):
            receipt('bind-intent');receipt('bound');receipt('armed')
        stack.enter_context(patch.object(drain,'gate_namespace',side_effect=gate))
        stack.enter_context(patch.object(drain,'public_registration',side_effect=registrations or (lambda slot:self.REG[slot])))
        return state,readiness

    def test_arm_checks_readiness_before_ANY_mutation_and_records_it(self):
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            def not_ready():raise drain.NotReady('slot 2 runner is online/idle, not online/busy')
            state,_=self.arm_fixture(stack,folder,ready=not_ready)
            with self.assertRaises(drain.NotReady):drain.arm(Path('/nix/store/g'),'g',Path('/nix/store/w'),'w',Path('/nix/store/v'),'v')
            self.assertFalse(state.exists());drain.write_hold.assert_not_called();drain.run.assert_not_called();drain.pin.assert_not_called()
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            state,readiness=self.arm_fixture(stack,folder)
            drain.arm(Path('/nix/store/g'),'g',Path('/nix/store/w'),'w',Path('/nix/store/v'),'v')
            manifest=json.loads((state/'manifest.json').read_text())
        self.assertEqual(manifest['readiness']['registrations']['3'],self.REG[3])
        for slot in '1234':
            gate=manifest['gates'][slot]
            self.assertEqual((gate['registration_pre_bind'],gate['idle_risk']),(self.REG[int(slot)],[]))
            self.assertLessEqual(int(gate['pre_bind_monotonic_us']),int(gate['bound_monotonic_us']))

    def test_arm_flags_a_new_pre_bind_registration_and_refuses_a_changed_controller(self):
        new={'repo':'xmit-dev/ultimator','id':None,'name':'hound-ci-2-ffffffffffff'}
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            state,_=self.arm_fixture(stack,folder,registrations=lambda slot:new if slot==2 else self.REG[slot])
            printed=stack.enter_context(patch('builtins.print'))
            drain.arm(Path('/nix/store/g'),'g',Path('/nix/store/w'),'w',Path('/nix/store/v'),'v')
            manifest=json.loads((state/'manifest.json').read_text())
        self.assertEqual(manifest['gates']['2']['idle_risk'],['new-registration-before-bind'])
        self.assertEqual(manifest['phase'],'armed-awaiting-job-completion')  # flagged, never refused
        self.assertIn('HOUND_CI_DRAIN_IDLE_RISK slot=2 new-registration-before-bind',[c.args[0] for c in printed.call_args_list])
        self.assertEqual(printed.call_args.args[0],'HOUND_CI_DRAIN_ARMED four-private-namespaces host-gh-unchanged no-signals no-job-stop idle_risk=2:new-registration-before-bind')
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            state,_=self.arm_fixture(stack,folder)
            printed=stack.enter_context(patch('builtins.print'))
            drain.arm(Path('/nix/store/g'),'g',Path('/nix/store/w'),'w',Path('/nix/store/v'),'v')
        self.assertTrue(printed.call_args.args[0].endswith(' idle_risk=none'))
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            moved=lambda:{'controllers':{str(s):{'pid':1000+s+(s==3),'invocation_id':f'{s:x}'*32} for s in range(1,5)},'registrations':{str(k):v for k,v in self.REG.items()}}
            state,_=self.arm_fixture(stack,folder,ready=moved)
            with self.assertRaisesRegex(RuntimeError,'Controller changed after the readiness check'):
                drain.arm(Path('/nix/store/g'),'g',Path('/nix/store/w'),'w',Path('/nix/store/v'),'v')
            drain.write_hold.assert_not_called()
            self.assertFalse(state.exists())  # pins are checked BEFORE the state directory exists
        with tempfile.TemporaryDirectory() as folder,ExitStack() as stack:
            state,_=self.arm_fixture(stack,folder)
            drain.pin.side_effect=RuntimeError('Only the exact known legacy supervisor may be drained')
            with self.assertRaisesRegex(RuntimeError,'legacy supervisor'):
                drain.arm(Path('/nix/store/g'),'g',Path('/nix/store/w'),'w',Path('/nix/store/v'),'v')
            self.assertFalse(state.exists());drain.write_hold.assert_not_called();drain.run.assert_not_called()

    def test_main_maps_not_ready_to_exit_75_with_one_line(self):
        argv=['drain-old.py','--gate','/nix/store/g','--gate-sha256','g','--waiter-source','/nix/store/w','--waiter-sha256','w','--validator-source','/nix/store/v','--validator-sha256','v']
        with patch.object(drain.sys,'argv',argv),patch.object(drain,'require_pinned_interpreter'),patch.object(drain,'wrapper_resolves_gate'),\
             patch.object(drain,'arm',side_effect=drain.NotReady('slot 1 runner is online/idle, not online/busy')),patch('builtins.print') as printed:
            with self.assertRaises(SystemExit) as stopped:drain.main()
        self.assertEqual(stopped.exception.code,75)
        self.assertEqual(printed.call_args.args[0],'HOUND_CI_DRAIN_NOT_READY slot 1 runner is online/idle, not online/busy; nothing changed, re-invoke later')

    def test_production_gate_isolated_shebang(self):
        first=Path(__file__).with_name('drain-gh-gate.py').read_text().splitlines()[0]
        self.assertEqual(first,'#!/nix/store/d64q19q1xjdwfhqx6czvrjgrhq0n3lcc-python3-3.14.7/bin/python3 -IB')

    def test_source_order_and_no_signal_restart_credentials_or_payload_reads(self):
        source=Path(__file__).with_name('drain-old.py').read_text()
        self.assertLess(source.index("if any(entry['boot_id'] != boot_id"),source.rindex('write_hold(entry, manifest)'))
        self.assertLess(source.index("if any(entry['boot_id'] != boot_id"),source.index('STATE.mkdir(mode=0o700)'))  # pins before state
        self.assertIn("'first_registration_read': dict(before['first_registration_read'], operator_sha256=manifest['operator_sha256'])",source)
        self.assertLess(source.index("properties(entry['slot'])['Restart'] != 'no'"),source.index('gate_namespace(entry, gate, receipt)'))
        self.assertNotIn('os.kill(',source)
        self.assertNotIn("['systemctl', 'stop'",source)
        self.assertNotIn("['systemctl', 'start'",source)
        self.assertNotIn('/environ',source)
        self.assertNotIn('seed.iso',source)
        self.assertNotIn('hosts.yml',source)
        self.assertNotIn('CREDENTIALS_DIRECTORY',source)


if __name__=='__main__':unittest.main()
