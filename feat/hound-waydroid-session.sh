#!/run/current-system/sw/bin/bash
set -euo pipefail
umask 077
export PATH=/run/wrappers/bin:/run/current-system/sw/bin:/etc/profiles/per-user/pcarrier/bin:/nix/var/nix/profiles/default/bin
[[ $(hostname -s) == hound && $(id -u) == 1000 ]]
[[ $(findmnt -rn -o SOURCE -M /var/lib/waydroid) == tank/var/devbox-waydroid-system-waydroid-acl-20261002T082100Z ]]
[[ $(findmnt -rn -o SOURCE -M /home/pcarrier/.local/share/waydroid) == tank/home/devbox-waydroid-user-waydroid-acl-20261002T082100Z ]]
sock=/run/user/1000/yas/yas-ultimator-xhyecq2fvbzfp4ca.sock
# One initial check and kernel filesystem events, not a polling loop. Arm the
# watch before checking so socket creation between those steps cannot be lost.
/nix/store/0r99vylsrphb6c2pw6fdyi8labmp2pj2-nodejs-26.10.0/bin/node --input-type=module - "$sock" <<'NODE'
import {watch,statSync} from 'node:fs';
import {dirname} from 'node:path';
const target=process.argv[2],parent=dirname(target);let done=false,wParent,parentInode;
const wRoot=watch('/run/user/1000',()=>check());
const timer=setTimeout(()=>finish(1,'Timed out awaiting private devbox YAS socket creation event'),120000);
function finish(code,text){if(done)return;done=true;clearTimeout(timer);wRoot.close();wParent?.close();if(text)console.error(text);process.exitCode=code;}
function check(){if(done)return;try{const s=statSync(parent);if(!s.isDirectory())return finish(1,'Private YAS parent is not a directory');if(parentInode!==s.ino){wParent?.close();wParent=watch(parent,()=>check());parentInode=s.ino;wParent.on('error',e=>finish(1,e.message));}}catch(e){if(e.code!=='ENOENT')return finish(1,e.message);wParent?.close();wParent=undefined;parentInode=undefined;}try{if(statSync(target).isSocket())finish(0);}catch(e){if(e.code!=='ENOENT')finish(1,e.message);}}
wRoot.on('error',e=>finish(1,e.message));check();
NODE
desktop=$(/srv/devbox/yas-bin/yas --on "socket:$sock" run /run/current-system/sw/bin/bash -c 'set -euo pipefail; [[ $(hostname -s) == hound && $(id -u) == 1000 ]]; for k in XDG_RUNTIME_DIR WAYLAND_DISPLAY PULSE_RUNTIME_PATH PULSE_SERVER PIPEWIRE_REMOTE DBUS_SESSION_BUS_ADDRESS; do [[ -n ${!k-} ]]; printf "%s\t%s\n" "$k" "${!k}"; done' </dev/null)
count=0
while IFS=$'\t' read -r key value; do
  case "$key" in XDG_RUNTIME_DIR|WAYLAND_DISPLAY|PULSE_RUNTIME_PATH|PULSE_SERVER|PIPEWIRE_REMOTE|DBUS_SESSION_BUS_ADDRESS) export "$key=$value"; ((count+=1));; *) echo 'Unexpected private desktop environment field' >&2; exit 1;; esac
done <<<"$desktop"
[[ $count == 6 && $XDG_RUNTIME_DIR == /run/user/1000 ]]
[[ $WAYLAND_DISPLAY == wayland-* && $WAYLAND_DISPLAY != */* && -S $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY ]]
[[ $PULSE_RUNTIME_PATH == /run/user/1000/yas-audio-*/pulse && -S $PULSE_RUNTIME_PATH/native ]]
[[ $PULSE_SERVER == "unix:$PULSE_RUNTIME_PATH/native" ]]
[[ $PIPEWIRE_REMOTE == /run/user/1000/yas-audio-*/pipewire-0 && -S $PIPEWIRE_REMOTE ]]
[[ $DBUS_SESSION_BUS_ADDRESS == unix:path=/tmp/dbus-* ]]
bus=${DBUS_SESSION_BUS_ADDRESS#unix:path=}; bus=${bus%%,*}; [[ -S $bus ]]
systemctl is-active --quiet waydroid-container.service
# No host/global desktop environment import. Only this unit's own process gets
# the actual fresh private compositor, audio and desktop bus environment.
echo 'Waydroid launch guards PASS: physical hound, exact user/system datasets, fresh devbox-private Wayland/Pulse/PipeWire/D-Bus sockets.'
exec /run/current-system/sw/bin/waydroid session start
