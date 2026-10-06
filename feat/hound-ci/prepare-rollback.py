#!/usr/bin/env python3
"""Only enhance the already captured root rollback ledger; no service changes."""
import hashlib,json,os,stat
from datetime import datetime,timezone
from pathlib import Path

BASE=Path('/var/lib/hound-ci/rollout-cache-v2-backup-20261005')
ROOTS=Path('/nix/var/nix/gcroots/hound-ci')
ENABLE=Path('/etc/systemd/system.attached/multi-user.target.wants')
PROFILE='/nix/store/qznhfs183818151n112yjgiwnywrafag-nixos-system-hound-26.11.20260922.6774f7b'
OLD_IMAGE=Path('/var/lib/hound-ci/base.qcow2')
OLD_SHA='0e856d33b2e9c08e54863b3916d7a3aef7fce69d513f7f163450e3a015d9056b'
OLD={1:'kd6iv3h531lwrm23mphjhagy631nijml',2:'wi7drli2brmzkmxfl0m304hsqgwfwh1i',3:'imy7dlrpv7c4z8wc5489xw9g3czlc8wa',4:'bp5vhdqy13wnrfimx8gk7scbahryw7xn'}
AUX={'hound-ci-firewall.service':'k618jhqnffah1nhi0dndf38mk9vvn9x1',
     'hound-ci-image.service':'kc030x0hfhl6dxy3q3jb0550z5gzniqa',
     'hound-ci.slice':'zy26m2737sahqfl7xypqgvfrjbq1pb07',
     'hound-ci-storage.service':'l2jhgl36f17yd5wg005plrbiapy01ky7'}


def require(ok,message):
    if not ok:raise RuntimeError(message)


def read(path,limit=65536,mode=0o600):
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        m=os.fstat(fd)
        require(stat.S_ISREG(m.st_mode) and m.st_uid==m.st_gid==0 and stat.S_IMODE(m.st_mode)==mode and 0<m.st_size<=limit,'Unexpected retained public file metadata')
        value=os.read(fd,limit+1);require(len(value)<=limit,'Read bound exceeded');return value
    finally:os.close(fd)


def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def exclusive(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    fsync_dir(path.parent)


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def main():
    require(os.geteuid()==0,'Root operator required')
    meta=BASE.lstat();require(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and stat.S_IMODE(meta.st_mode)==0o700,'Existing rollback directory mismatch')
    manifest_path=BASE/'rollback-manifest.json';raw=read(manifest_path);old=json.loads(raw)
    require(set(old)=={'host_profile','old_unit_links',str(ROOTS),str(ENABLE)},'Only original schema accepted; never duplicate upgrade')
    for name in ('rollback-upgrade-intent.json','rollback-manifest-v1.json','rollback-manifest-v2.tmp','rollback-upgrade-complete.json'):
        try:(BASE/name).lstat()
        except FileNotFoundError:pass
        else:raise RuntimeError('Existing rollback upgrade artifact; never automatically retry or roll back')
    require(os.readlink('/run/current-system')==old['host_profile']==PROFILE,'Host baseline drift')
    require(len(old['old_unit_links'])==4,'Original four unit links required')
    out={'schema':2,'host_profile':PROFILE,'host_profile_resolved':str(Path('/run/current-system').resolve(strict=True)),
         'old_image':{'path':str(OLD_IMAGE),'sha256':OLD_SHA},'old_unit_links':[]}
    for slot,prefix in OLD.items():
        name=f'hound-ci-{slot}.service';target=f'/nix/store/{prefix}-unit-{name}/{name}'
        link=f'/etc/systemd/system.attached/{name}'
        matches=[entry for entry in old['old_unit_links'] if entry=={'unit':name,'path':link,'target':target}]
        require(len(matches)==1 and os.readlink(link)==target,'Original attached link mismatch')
        meta=Path(link).lstat();require(stat.S_ISLNK(meta.st_mode) and meta.st_uid==meta.st_gid==0,'Original link ownership mismatch')
        original=read(target,mode=0o444);copy=read(BASE/name)
        require(original==copy,'Retained unit copy differs from original immutable target')
        out['old_unit_links'].append({'unit':name,'path':link,'target':target,'uid':0,'gid':0,'sha256':hashlib.sha256(original).hexdigest(),'backup':str(BASE/name)})
    for directory in (ROOTS,ENABLE):
        entries=old[str(directory)];controllers={f'hound-ci-{slot}.service' for slot in OLD}
        require(len(entries)==4 and {Path(entry['path']).name for entry in entries}==controllers,'Original controller root/enable inventory incomplete')
        expected={f'hound-ci-{slot}.service':prefix for slot,prefix in OLD.items()}
        expected.update(AUX if directory==ROOTS else {'hound-ci-firewall.service':AUX['hound-ci-firewall.service']})
        require({entry.name for entry in directory.iterdir()}==set(expected),'Full retained root/enable inventory drift')
        for entry in entries:
            require(set(entry)=={'path','target'} and Path(entry['path']).parent==directory,'Fixed original inventory path mismatch')
            name=Path(entry['path']).name
            wanted=f'/nix/store/{expected[name]}-unit-{name}' + (f'/{name}' if directory==ENABLE else '')
            require(entry['target']==wanted,'Original captured controller inventory differs')
        values=[]
        for name,prefix in sorted(expected.items()):
            path=directory/name;wanted=f'/nix/store/{prefix}-unit-{name}' + (f'/{name}' if directory==ENABLE else '')
            require(os.readlink(path)==wanted,'Full retained root/enable target mismatch')
            meta=path.lstat();require(stat.S_ISLNK(meta.st_mode) and meta.st_uid==meta.st_gid==0,'Retained root/enable ownership mismatch')
            require(Path(wanted).exists(),'Original immutable retained root/enable target missing')
            values.append({'path':str(path),'target':wanted,'uid':0,'gid':0})
        out[str(directory)]=values
    fd=os.open(OLD_IMAGE,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0 and stat.S_IMODE(before.st_mode)==0o444,'Old immutable image metadata mismatch')
        with os.fdopen(fd,'rb',closefd=False) as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        after=os.fstat(fd);require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) and actual==OLD_SHA,'Old immutable image hash/identity changed')
    finally:os.close(fd)
    require(read(manifest_path)==raw and os.readlink('/run/current-system')==PROFILE,'Rollback manifest/profile changed during qualification')
    new_raw=json.dumps(out,indent=2,sort_keys=True).encode()+b'\n'
    intent={'phase':'rollback-schema-upgrade-intent','timestamp_utc':utc_timestamp(),'old_manifest_sha256':hashlib.sha256(raw).hexdigest(),'new_manifest_sha256':hashlib.sha256(new_raw).hexdigest()}
    exclusive(BASE/'rollback-upgrade-intent.json',(json.dumps(intent,sort_keys=True)+'\n').encode())
    exclusive(BASE/'rollback-manifest-v1.json',raw)
    temp=BASE/'rollback-manifest-v2.tmp';exclusive(temp,new_raw)
    temp.replace(manifest_path);fsync_dir(BASE)
    complete={'phase':'rollback-schema-2-complete','timestamp_utc':utc_timestamp()}
    exclusive(BASE/'rollback-upgrade-complete.json',(json.dumps(complete,sort_keys=True)+'\n').encode())
    print('ROLLBACK_SCHEMA_2_COMPLETE four-original-units-roots-enable-links old-image-hash profile-unchanged no-service-change')


if __name__=='__main__':main()
