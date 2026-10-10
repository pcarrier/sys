# Public Ultimator moves onto hound without replacing its compute, desktop,
# Docker engine, nginx vhosts or general-purpose GitHub runner. indentbox keeps
# Caddy/TLS during the initial cutover. Production state is never below /src.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  state = "/var/lib/ultimator";
  root = "${state}/ultimator";
  home = "${state}/home";
  ready = "${state}/.cluster-ready";
  logs = "/var/log/ultimator-deploy-main";
  tools = with pkgs; [
    bashInteractive
    coreutils
    curl
    direnv
    docker
    git
    openssh
    nix
    util-linux
  ];
  # The registration keeps its name and deployment-only labels for the existing
  # workflow. It is not hound's unrelated general-purpose runner.
  runner = "indentbox";
in
{
  users.groups.ultimator = { };
  users.users.ultimator = {
    isSystemUser = true;
    group = "ultimator";
    home = home;
    createHome = true;
    homeMode = "0700";
    shell = pkgs.bashInteractive;
    description = "Ultimator production stack";
  };
  users.groups.ultimator-runner = { };
  users.users.ultimator-runner = {
    isSystemUser = true;
    group = "ultimator-runner";
    home = "/var/lib/ultimator-runner-work";
    description = "Ultimator deployment-only GitHub runner";
  };

  systemd.tmpfiles.rules = [
    "d ${state} 0700 ultimator ultimator -"
    "d ${home} 0700 ultimator ultimator -"
    "f ${state}/land.lock 0600 ultimator ultimator -"
    "d /var/lib/ultimator-sandboxes 0755 root root -"
    "d /var/lib/ultimator-sandboxes/bin 0755 root root -"
    "d /var/lib/ultimator-sandboxes/home 0700 pcarrier users -"
    "d /var/lib/ultimator-sandboxes/credentials 0700 pcarrier users -"
    "d /var/lib/ultimator-sandboxes/uplink 0700 pcarrier users -"
    "d ${logs} 0755 ultimator ultimator 30d"
    "d /var/lib/ultimator-runner-work 0750 ultimator-runner ultimator-runner -"
    "d /var/lib/ultimator-runner-work/${runner} 0750 ultimator-runner ultimator-runner -"
    "d /var/lib/secrets 0700 root root -"
  ];

  # No shell/profile or credentials from pcarrier. bin/dev enters this exact
  # checkout's pinned shell and reads its private .env.local.
  systemd.services.ultimator = {
    description = "Ultimator production stack (hound)";
    wantedBy = [ "multi-user.target" ];
    wants = [
      "network-online.target"
      "garage.service"
      "ultimator-sandboxes.service"
    ];
    after = [
      "network-online.target"
      "tailscaled.service"
      "docker.service"
      "garage.service"
    ];
    unitConfig = {
      ConditionPathExists = [
        "${root}/.env.local"
        ready
      ];
      RequiresMountsFor = [
        state
        "/var/lib/private/garage"
      ];
    };
    path = tools;
    environment = {
      HOME = home;
      PC_DISABLE_TUI = "1";
      PC_CONFIG_FILES = "${root}/process-compose.yml,${state}/host.yml";
      ULTIMATOR_HOST = "127.0.0.1,100.77.9.102";
    };
    serviceConfig = {
      User = "ultimator";
      Group = "ultimator";
      WorkingDirectory = root;
      ExecStart = "${pkgs.bash}/bin/bash -c 'exec ${root}/bin/dev'";
      Restart = "on-failure";
      RestartSec = 10;
      KillMode = "mixed";
      TimeoutStopSec = 90;
      LimitNOFILE = 1048576;
      UMask = "0077";
      ProtectHome = true;
    };
  };

  # Same Garage version, node identity, LMDB metadata and data trees. The cold
  # copy must be complete before .cluster-ready is created; never dual-run.
  services.garage = {
    enable = true;
    package = pkgs.garage_2;
    environmentFile = "/var/lib/secrets/ultimator-garage.env";
    settings = {
      replication_factor = 1;
      db_engine = "lmdb";
      metadata_fsync = true;
      data_fsync = true;
      metadata_auto_snapshot_interval = "6h";
      rpc_bind_addr = "127.0.0.1:3901";
      rpc_public_addr = "127.0.0.1:3901";
      s3_api = {
        s3_region = "garage";
        api_bind_addr = "127.0.0.1:3900";
      };
      admin.api_bind_addr = "127.0.0.1:3903";
    };
  };
  systemd.services.garage.unitConfig.ConditionPathExists = ready;

  # Initial front door remains indentbox. Only its tailnet link needs to reach
  # this listener; no new public nginx/Caddy binding is introduced on hound.
  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ 8301 ];
  networking.firewall.interfaces.tailscale0.allowedUDPPorts = [ 4433 ];

  services.github-runners.${runner} = {
    enable = true;
    url = "https://github.com/xmit-dev";
    tokenFile = "/var/lib/secrets/ultimator-runner.token";
    name = runner;
    replace = true;
    runnerGroup = "deploy";
    noDefaultLabels = true;
    extraLabels = [
      "indentbox"
      "deploy"
    ];
    user = "ultimator-runner";
    group = "ultimator-runner";
    workDir = "/var/lib/ultimator-runner-work/${runner}";
    serviceOverrides = {
      InaccessiblePaths = [
        "/src"
        "${state}"
      ];
      CPUWeight = 20;
      IOWeight = 20;
      MemoryHigh = "2G";
      MemoryMax = "4G";
    };
  };
  systemd.services."github-runner-${runner}".unitConfig.ConditionPathExists = ready;

  security.polkit.extraConfig = ''
    polkit.addRule(function (action, subject) {
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "ultimator" && action.lookup("verb") == "restart" &&
          action.lookup("unit") == "ultimator.service") { return polkit.Result.YES; }
    });
    polkit.addRule(function (action, subject) {
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "ultimator-runner" && action.lookup("verb") == "start" &&
          /^ultimator-deploy-main@(dry-)?[0-9]+-[0-9]+\.service$/.test(action.lookup("unit"))) {
        return polkit.Result.YES;
      }
    });
  '';

  systemd.services."ultimator-deploy-main@" = {
    description = "Deploy Ultimator main to hound (run %i)";
    after = [ "ultimator.service" ];
    path = [ "/run/wrappers" ] ++ tools;
    environment = {
      HOME = home;
      DEPLOY_MAIN_ANNOTATE = "1";
      DEPLOY_MAIN_RESTART = "/run/current-system/sw/bin/systemctl restart ultimator.service";
    };
    restartIfChanged = false;
    stopIfChanged = false;
    unitConfig.CollectMode = "inactive-or-failed";
    serviceConfig = {
      Type = "oneshot";
      User = "ultimator";
      Group = "ultimator";
      WorkingDirectory = root;
      RuntimeDirectory = "ultimator-deploy-main/%i";
      BindPaths = [ "${state}/land.lock:/tmp/ultimator-land.lock" ];
      NoNewPrivileges = true;
      StandardOutput = "truncate:${logs}/%i.log";
      StandardError = "inherit";
      TimeoutStartSec = "110min";
      UMask = "0022";
      # The upstream deploy script only sees process-compose roles. Verify the
      # separately isolated worker before GitHub receives this unit's success.
      ExecStartPost = "+${pkgs.writeShellScript "verify-ultimator-sandbox-deploy" ''
        set -euo pipefail
        case "$1" in dry-*) exit 0 ;; esac
        exec 9>/var/lib/ultimator/land.lock
        ${pkgs.util-linux}/bin/flock -x 9
        ${pkgs.systemd}/bin/systemctl start ultimator-sandbox-projection.service
        ${pkgs.systemd}/bin/systemctl restart ultimator-sandboxes.service
        ${pkgs.systemd}/bin/systemctl is-active --quiet ultimator-sandboxes.service
        ${pkgs.diffutils}/bin/cmp ${root}/target/release/ultimatord /var/lib/ultimator-sandboxes/bin/ultimatord
      ''} %i";
      ProtectHome = true;
    };
    scriptArgs = "%i";
    script = ''
      run=$1
      args=()
      case "$run" in dry-*) args+=(--dry-run) ;; esac
      if [ -e .dev/deploy-hold ]; then
        echo "::error::Deploys are held: $(head -c 2000 .dev/deploy-hold | tr '\n' ' ') (${root}/.dev/deploy-hold on hound)"
        exit 1
      fi
      git fetch --quiet origin main
      main=$(git rev-parse refs/remotes/origin/main)
      git show "$main:bin/deploy-main" >"$RUNTIME_DIRECTORY/deploy-main"
      exec bash "$RUNTIME_DIRECTORY/deploy-main" "''${args[@]}" "$main"
    '';
  };
}
