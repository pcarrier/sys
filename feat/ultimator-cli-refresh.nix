# Not a module: a function that makes the script keeping a hand-placed Ultimator CLI current, for the computers that
# run one (feat/forge-deploy.nix's forge-deploy-computer, feat/hound-devbox.nix's ultimator-devbox). Import it with
#   refreshCli = import ./ultimator-cli-refresh.nix { inherit pkgs; name = …; label = …; bin = …; stamp = …; stage = …; };
#
# The CLI as ultimator.app serves it, brought up to date: the computers run it at each start (ExecStartPre) and daily.
# It runs install.sh only when SHA256SUMS names another build than the one installed (so a crash loop doesn't download
# 40 MB every few seconds), installs into a staging directory made under `stage` and renames the binary over the old
# one (`stage` must be on bin's filesystem: the rename is then atomic, and a running computer keeps its own inode, so
# nothing that runs is replaced in place). When it can't (ultimator.app down, no network at boot, a directory it may
# not write), it keeps the binary it has and says so, exiting 0: the computer still starts. Only a first install with
# nothing to keep fails.
{
  pkgs,
  # The script's name in the store.
  name,
  # What its log lines start with.
  label,
  # The CLI it keeps current.
  bin,
  # Where it notes the SHA-256 of the build it installed.
  stamp,
  # The directory its staging directory goes in: on bin's filesystem.
  stage,
}:
let
  # The download this host's CLI comes from, which install.sh picks the same way.
  download = "ultimator-${
    if pkgs.stdenv.hostPlatform.isAarch64 then "linux-aarch64" else "linux-x86_64"
  }.gz";
in
pkgs.writeShellScript name ''
  set -u
  export PATH=${
    pkgs.lib.makeBinPath [
      pkgs.coreutils
      pkgs.curl
      pkgs.gawk
      pkgs.gnugrep
      pkgs.gzip
      pkgs.getconf
    ]
  }:$PATH
  stamp=${stamp}
  keep() {
    if [ -x ${bin} ]; then
      echo "${label}: $*; keeping $(${bin} --version 2>/dev/null | head -n 1 || echo the installed CLI)" >&2
      exit 0
    fi
    echo "${label}: $*; no CLI installed yet" >&2
    exit 1
  }
  sums=$(curl -fsSL --max-time 60 https://ultimator.app/downloads/SHA256SUMS) || keep "could not fetch SHA256SUMS"
  want=$(printf '%s\n' "$sums" | awk -v f=${download} '$2 == f || $2 == "*" f { print $1 }')
  [ -n "$want" ] || keep "${download} is not in SHA256SUMS"
  if [ -x ${bin} ] && [ "$(cat "$stamp" 2>/dev/null)" = "$want" ]; then
    exit 0
  fi
  stage=$(mktemp -d ${stage}/.cli-XXXXXX) || keep "could not make a staging directory"
  trap 'rm -rf "$stage"' EXIT
  curl -fsSL --max-time 60 -o "$stage/install.sh" https://ultimator.app/install.sh || keep "could not fetch install.sh"
  TMPDIR=$stage ULTIMATOR_INSTALL_DIR=$stage/bin ${pkgs.bash}/bin/sh "$stage/install.sh" >&2 \
    || keep "install.sh failed"
  "$stage/bin/ultimator" --version >/dev/null 2>&1 || keep "the new CLI does not run"
  mv -f "$stage/bin/ultimator" ${bin} || keep "could not put the new CLI in place"
  printf '%s\n' "$want" >"$stamp"
  echo "${label}: installed $(${bin} --version 2>/dev/null | head -n 1)" >&2
''
