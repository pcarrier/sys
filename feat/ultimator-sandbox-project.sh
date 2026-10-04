#!/usr/bin/env bash
# Root publishes only the sandbox role's state. Read all core inputs as its
# unprivileged owner; symlinks/races there can never make root read other data.
set -euo pipefail
umask 077
restart_worker=0
src=/var/lib/ultimator/ultimator
dst=/var/lib/ultimator-sandboxes
test -f /var/lib/ultimator/.cluster-ready
for directory in "$dst" "$dst/bin" "$dst/credentials" "$dst/uplink"; do
  test -d "$directory" && test ! -L "$directory"
done
test "$(stat -c %u "$dst")" = 0
test "$(stat -c %u "$dst/bin")" = 0
# Root-owned staging is on the same filesystem as the publish destinations.
# Never create root-written temporary files inside a compute-writable directory.
install -d -o root -g root -m 0700 "$dst/.publish"
test ! -L "$dst/.publish"
work=$(mktemp -d "$dst/.publish/run.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
read_core() { runuser -u ultimator -- cat -- "$1"; }
for name in ultimatord ultimator; do
  read_core "$src/target/release/$name" > "$work/$name"
  test -s "$work/$name"
  if ! cmp -s "$work/$name" "$dst/bin/$name"; then
    chmod 0755 "$work/$name"
    mv -Tf "$work/$name" "$dst/bin/$name"
  fi
done
shopt -s nullglob
files=("$src/.dev/credentials/sandbox.token" "$src/.dev/credentials/"sandbox@*.token)
for from in "${files[@]}"; do
  name=${from##*/}
  read_core "$from" > "$work/$name"
  test -s "$work/$name"
  if ! cmp -s "$work/$name" "$dst/credentials/$name"; then
    if test -e "$dst/credentials/$name"; then restart_worker=1; fi
    chmod 0600 "$work/$name"
    chown pcarrier:users "$work/$name"
    mv -Tf "$work/$name" "$dst/credentials/$name"
  fi
done
# S3 access is required by image reads/artifacts/long outputs; no admin or
# unrelated worker token and no provider/notification key is provisioned here.
read_core "$src/.env.local" | grep -E '^ULTIMATOR_(BLOBS|S3_ENDPOINT|S3_REGION|S3_ACCESS_KEY_ID|S3_SECRET_ACCESS_KEY|S3_SESSION_TOKEN)=' > "$work/runtime.env"
test -s "$work/runtime.env"
chmod 0600 "$work/runtime.env"
if ! cmp -s "$work/runtime.env" "$dst/runtime.env"; then
  if test -e "$dst/runtime.env"; then restart_worker=1; fi
  mv -Tf "$work/runtime.env" "$dst/runtime.env"
fi
# Do not prune retired credentials/uplink identities during host migration.

# Updating a captured role/S3 credential needs a drained worker restart. Never
# wait for its job while it is ordered after this oneshot. Initial publication
# does not enqueue a restart of the worker which is still waiting to start.
if test "$restart_worker" = 1 && systemctl is-active --quiet ultimator-sandboxes.service; then
  systemctl --no-block try-restart ultimator-sandboxes.service
fi
