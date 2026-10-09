# Forge's CD executor on this host (xmit-dev/ultimator's .ultimator/workflows/deploy.yml, docs/CI.md "Executors"):
# the Ultimator computer `<host>-deploy`, which the organization's CI executor labelled `hound` and `deploy` runs
# jobs on, the way feat/ultimator-cluster-cd.nix's GitHub runner does today. Both may run side by side: each starts
# the same release unit, which takes the same lock, so two releases never overlap.
#
# The computer runs as its own user, forge-deploy (not pcarrier, not the CI runners' users), with no sudo. Its jobs'
# runner (`ultimator ci job`, which the job's launch starts) may do one thing beyond what any user may (polkit,
# below): start ultimator-cluster-release@forge-<run>-<attempt>.service (`forge-dry-` for a dry run), where <run> is
# Forge's run ID: letters and digits. The unit is feat/ultimator-cluster-cd.nix's, as pcarrier, unchanged but for
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
    path = [
      "${home}/bin"
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
      # The CLI as ultimator.app serves it (it keeps itself current), installed on the first start.
      ExecStartPre = pkgs.writeShellScript "forge-deploy-install" ''
        set -eu
        if [ ! -x ${bin} ]; then
          curl -fsSL https://ultimator.app/install.sh | ULTIMATOR_INSTALL_DIR=${home}/bin ${pkgs.bash}/bin/sh
        fi
      '';
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

  security.polkit.extraConfig = ''
    // Forge's deploy executor (feat/forge-deploy.nix) may start releases of Ultimator's main, and nothing else.
    polkit.addRule(function (action, subject) {
      if (action.id == "org.freedesktop.systemd1.manage-units" &&
          subject.user == "forge-deploy" &&
          action.lookup("verb") == "start" &&
          /^ultimator-cluster-release@forge-(dry-)?[A-Za-z0-9_]+-[0-9]+\.service$/.test(action.lookup("unit"))) {
        return polkit.Result.YES;
      }
    });
  '';
}
