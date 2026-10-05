#!/usr/bin/env bash
# Trusted bootstrap; only the repository-bound single-run JIT config enters the VM.
set -euo pipefail
trap 'systemctl poweroff' EXIT
export PATH=/home/runner/.cargo/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/home/runner
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
printf 'HOUND_CI_GUEST_PREFLIGHT_OK\n'
# JIT is scoped to one runner/id and one job, not an hour-long reusable registration token.
jit="$(jq -r .jit /etc/hound-ci-registration.json)"
rm -f /etc/hound-ci-registration.json
cd /opt/actions-runner
# Avoid secret arguments in host process lists: this command executes only inside VM.
runuser -u runner -- env HOME=/home/runner PATH="$PATH" ./run.sh --jitconfig "$jit"
unset jit
printf 'HOUND_CI_GUEST_JOB_FINISHED\n'
