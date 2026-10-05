#!/usr/bin/env bash
# Trusted bootstrap; only the repository-bound single-run JIT config enters the VM.
set -euo pipefail
trap 'systemctl poweroff' EXIT
export PATH=/home/runner/.cargo/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/home/runner
# Keep the current generation usable until the explicitly approved switch.
# Old images use the runner-owned normal cache; only qualified seeds use /opt.
if [[ -f /etc/hound-ci/cache-manifest.json ]]; then
  export RUNNER_TOOL_CACHE=/opt/hostedtoolcache
else
  export RUNNER_TOOL_CACHE=/opt/actions-runner/_work/_tool
  runuser -u runner -- mkdir -p "$RUNNER_TOOL_CACHE"
fi
export CARGO_HOME=/home/runner/.cargo
export RUSTUP_HOME=/home/runner/.rustup
systemctl start docker
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64
test "$(rustc --version | awk '{print $2}')" = 1.99.0
helm version --short >/dev/null
gh --version >/dev/null
pwsh --version >/dev/null
python3 --version >/dev/null
/usr/bin/google-chrome --version
runuser -u runner -- docker info >/dev/null
runuser -u runner -- xvfb-run --auto-servernum /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1"); from gi.repository import Gtk, WebKit2; Gtk.init([]); view=WebKit2.WebView(); print("WebKit2 GTK/Xvfb preflight OK")'
# No --no-sandbox: this exercises the native VM/user-namespace Chrome sandbox.
runuser -u runner -- /usr/bin/google-chrome --headless --disable-gpu --dump-dom 'data:text/html,<body>hound-ci-sandbox-ok</body>' >/tmp/chrome-preflight.html 2>/tmp/chrome-preflight.err
grep -q hound-ci-sandbox-ok /tmp/chrome-preflight.html
# QEMU SLIRP maps this to host loopback; the host UID firewall must deny it.
python3 - <<'PY'
import socket
for host in ('10.0.2.2', '192.168.1.150', '100.77.9.102', '169.254.169.254'):
    with socket.socket() as stream:
        stream.settimeout(2)
        try:
            stream.connect((host, 22))
        except OSError:
            continue
        raise SystemExit('Host/private network unexpectedly reachable')
PY
# Bounded offline ledger/preflight rejects partial seeds before receiving a job.
if [[ -f /etc/hound-ci/cache-manifest.json ]]; then
  python3 /opt/hound-ci-cache.py preflight --pins /etc/hound-ci/cache-pins.json
fi
printf 'HOUND_CI_GUEST_PREFLIGHT_OK\n'
# JIT is scoped to one runner/id and one job, not an hour-long reusable registration token.
jit="$(jq -r .jit /etc/hound-ci-registration.json)"
rm -f /etc/hound-ci-registration.json
cd /opt/actions-runner
# Avoid secret arguments in host process lists: this command executes only inside VM.
runuser -u runner -- env -i HOME=/home/runner USER=runner LOGNAME=runner \
  LANG=C.UTF-8 PATH="$PATH" CARGO_HOME="$CARGO_HOME" RUSTUP_HOME="$RUSTUP_HOME" \
  RUNNER_TOOL_CACHE="$RUNNER_TOOL_CACHE" ./run.sh --jitconfig "$jit"
unset jit
printf 'HOUND_CI_GUEST_JOB_FINISHED\n'
