# Forge's CD executor on this host (xmit-dev/ultimator's .ultimator/workflows/deploy.yml, docs/CI.md "Executors"):
# the Ultimator computer `<host>-deploy`, which the organization's CI executor labelled `hound` and `deploy` runs
# jobs on, the way feat/ultimator-cluster-cd.nix's GitHub runner does today. Both may run side by side: each starts
# the same release unit, which takes the same lock, so two releases never overlap.
#
# The computer runs as its own user, forge-deploy (not pcarrier, not the CI runners' users), with no sudo. Its jobs'
# runner (`ultimator ci job`, which the job's launch starts) may do one thing beyond what any user may (polkit,
# below): start ultimator-cluster-release@forge-<run>-<attempt>.service (`forge-dry-` for a dry run), where <run> is
# Forge's run ID: letters and digits. It may also start ultimator-release-build@<commit>.service
# (feat/ultimator-cluster-cd.nix), which builds a merge-queue candidate's release ahead of its merge and releases nothing.
# Executor, once: `ultimator ci executor set hound,prebuild --machine hound-deploy --max 2`. The release unit is feat/ultimator-cluster-cd.nix's, as pcarrier, unchanged but for
# reading `forge-dry-` as a dry run; it fetches main from GitHub, which Forge's one-way mirror keeps equal to Forge's
# main until Ultimator moves (P4 points it at Forge).
#
# Once, by hand, after the first switch (the computer's sign-in is a person's):
#   sudo -u forge-deploy -H /var/lib/forge-deploy/bin/ultimator login     # as Pierre, in the browser it prints
#   sudo systemctl restart forge-deploy-computer
# then, in Ultimator: `ultimator ci executor set hound,deploy --machine hound-deploy --max 1` (an integration
# manager). The computer registers itself as the organization's (--org) on its first start.
{
  config,
  pkgs,
  ...
}:
let
  host = config.networking.hostName;
  name = "${host}-deploy";
  home = "/var/lib/forge-deploy";
  bin = "${home}/bin/ultimator";
  # The download this host's CLI comes from, which install.sh picks the same way.
  download = "ultimator-${if pkgs.stdenv.hostPlatform.isAarch64 then "linux-aarch64" else "linux-x86_64"}.gz";

  # The CLI as ultimator.app serves it, brought up to date: at each start of the computer (ExecStartPre) and daily
  # (forge-deploy-cli-refresh). It runs install.sh only when SHA256SUMS names another build than the one installed
  # (so a crash loop doesn't download 40 MB every 10 s), installs into a staging directory beside bin/ and renames the
  # binary over the old one (one filesystem: atomic, and the running computer keeps its own inode). When it can't
  # (ultimator.app down, no network at boot), it keeps the binary it has and says so: the computer still starts. Only
  # a first install with nothing to keep fails.
  refreshCli = pkgs.writeShellScript "forge-deploy-refresh-cli" ''
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
    stamp=${home}/.ultimator-cli.sha256
    keep() {
      if [ -x ${bin} ]; then
        echo "forge-deploy: $*; keeping $(${bin} --version 2>/dev/null | head -n 1 || echo the installed CLI)" >&2
        exit 0
      fi
      echo "forge-deploy: $*; no CLI installed yet" >&2
      exit 1
    }
    sums=$(curl -fsSL --max-time 60 https://ultimator.app/downloads/SHA256SUMS) || keep "could not fetch SHA256SUMS"
    want=$(printf '%s\n' "$sums" | awk -v f=${download} '$2 == f || $2 == "*" f { print $1 }')
    [ -n "$want" ] || keep "${download} is not in SHA256SUMS"
    if [ -x ${bin} ] && [ "$(cat "$stamp" 2>/dev/null)" = "$want" ]; then
      exit 0
    fi
    stage=$(mktemp -d ${home}/.cli-XXXXXX) || keep "could not make a staging directory"
    trap 'rm -rf "$stage"' EXIT
    curl -fsSL --max-time 60 -o "$stage/install.sh" https://ultimator.app/install.sh || keep "could not fetch install.sh"
    TMPDIR=$stage ULTIMATOR_INSTALL_DIR=$stage/bin ${pkgs.bash}/bin/sh "$stage/install.sh" >&2 \
      || keep "install.sh failed"
    "$stage/bin/ultimator" --version >/dev/null 2>&1 || keep "the new CLI does not run"
    mv -f "$stage/bin/ultimator" ${bin} || keep "could not put the new CLI in place"
    printf '%s\n' "$want" >"$stamp"
    echo "forge-deploy: installed $(${bin} --version 2>/dev/null | head -n 1)" >&2
  '';
in
{
  users = {
    users.forge-deploy = {
      isSystemUser = true;
      group = "forge-deploy";
      inherit home;
      createHome = true;
      shell = pkgs.bashInteractive;
      description = "Forge CI's deploy executor (Ultimator computer ${name})";
    };
    groups.forge-deploy = { };
  };

  systemd.tmpfiles.rules = [
    "d ${home} 0750 forge-deploy forge-deploy -"
    "d ${home}/bin 0750 forge-deploy forge-deploy -"
    "d ${home}/work 0750 forge-deploy forge-deploy -"
  ];

  systemd.services.forge-deploy-computer = {
    description = "Forge CI's deploy executor: the Ultimator computer ${name}";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    # The job's steps run here: systemctl and tail (deploy.yml), the runner's git and tar.
    # NixOS adds each entry's bin/ (and sbin/): home puts ${home}/bin, where the CLI is, on PATH.
    path = [
      home
      pkgs.curl
      pkgs.git
      pkgs.gnutar
      pkgs.gzip
      "/run/current-system/sw"
    ];
    environment = {
      HOME = home;
      ULTIMATOR_URL = "https://ultimator.app";
    };
    serviceConfig = {
      User = "forge-deploy";
      Group = "forge-deploy";
      WorkingDirectory = home;
      # The CLI as ultimator.app serves it, refreshed at each start (refreshCli, above): installed only once, it went
      # stale (a 10-10 CLI that read YAML workflows failed Forge CD runs #53-#55 on 10-11).
      ExecStartPre = refreshCli;
      ExecStart = "${bin} computer start ${name} --org --workspace ${home}/work --concurrency 4 --config ${home}/config.json";
      Restart = "always";
      RestartSec = 10;
      # A runner started detached (setsid) is the job's: a restart of the computer leaves it running to its job's end.
      KillMode = "process";
      # Its jobs start a unit and follow its log: nothing of anyone's checkouts or the release's state.
      InaccessiblePaths = [
        "/src"
        "/home"
        "/var/lib/ultimator-cluster-release"
        "-/var/lib/github-runner-work"
        "-/var/lib/github-deploy-work"
      ];
      ReadOnlyPaths = [ "/var/log/ultimator-cluster-release" ];
      NoNewPrivileges = true;
      CPUWeight = 20;
      IOWeight = 20;
      MemoryHigh = "2G";
      MemoryMax = "4G";
    };
  };

  # A computer that runs for weeks would keep the CLI it started with: refresh it daily, and restart the computer
  # when its binary was replaced (its /proc/PID/exe then reads "(deleted)"), unless a release from Forge is running
  # (it waits for the next day then). A restart leaves a job's detached runner running (KillMode=process, above).
  systemd.services.forge-deploy-cli-refresh = {
    description = "Refresh the Ultimator CLI of the computer ${name}";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment.HOME = home;
    serviceConfig = {
      Type = "oneshot";
      User = "forge-deploy";
      Group = "forge-deploy";
      WorkingDirectory = home;
      ExecStart = refreshCli;
      # "+": as root, to read the computer's /proc/PID/exe and restart it.
      ExecStartPost = "+${pkgs.writeShellScript "forge-deploy-restart-if-stale" ''
        set -u
        export PATH=${
          pkgs.lib.makeBinPath [
            pkgs.coreutils
            pkgs.gnugrep
            config.systemd.package
          ]
        }
        pid=$(systemctl show -p MainPID --value forge-deploy-computer.service)
        [ -n "$pid" ] && [ "$pid" != 0 ] || exit 0
        case "$(readlink /proc/"$pid"/exe 2>/dev/null)" in
          *" (deleted)") ;;
          *) exit 0 ;;
        esac
        if systemctl list-units --plain --no-legend --state=active 'ultimator-cluster-release@forge-*' | grep -q .; then
          echo "forge-deploy: a release is running; the computer restarts on the new CLI next time" >&2
          exit 0
        fi
        echo "forge-deploy: restarting the computer on the new CLI" >&2
        systemctl try-restart forge-deploy-computer.service
      ''}";
      NoNewPrivileges = true;
    };
  };
  systemd.timers.forge-deploy-cli-refresh = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      RandomizedDelaySec = "1h";
      Persistent = true;
    };
  };

  security.polkit.extraConfig = ''
    // Forge's deploy executor (feat/forge-deploy.nix) may start releases of Ultimator's main and prebuilds of its merge-queue
    // candidates, and nothing else.
    polkit.addRule(function (action, subject) {
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "forge-deploy" &&
          action.lookup("verb") == "start" &&
          /^ultimator-cluster-release@forge-(dry-)?[A-Za-z0-9_]+-[0-9]+\.service$/.test(action.lookup("unit"))) {
        return polkit.Result.YES;
      }
      // …and the merge queue's release prebuilds (feat/ultimator-cluster-cd.nix): a commit ID, which the unit checks
      // with Forge before building anything.
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "forge-deploy" &&
          action.lookup("verb") == "start" &&
          /^ultimator-release-build@[0-9a-f]{40}\.service$/.test(action.lookup("unit"))) {
        return polkit.Result.YES;
      }
    });
  '';
}
