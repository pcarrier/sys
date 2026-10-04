# The sandbox controller on indentbox reaches hound's Docker API over SSH.
# Reuse authenticated connections instead of creating a handshake per request.
{
  config,
  lib,
  ...
}:
{
  config = lib.mkMerge [
    (lib.mkIf (config.networking.hostName == "hound") {
      services.openssh.extraConfig = lib.mkAfter ''
        Match User pcarrier Address 100.110.157.123
          MaxSessions 128
        Match all
      '';
    })
    (lib.mkIf (config.networking.hostName == "indentbox") {
      home-manager.users.pcarrier =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          controlDirectory = "${config.home.homeDirectory}/.ssh/ultimator-control";
        in
        {
          programs.ssh.settings."Match originalhost hound user pcarrier" = {
            ControlMaster = "auto";
            ControlPath = "${controlDirectory}/%C";
            ControlPersist = "60s";
          };
          home.activation.ultimatorSshControlDirectory = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
            controlDirectory=${lib.escapeShellArg controlDirectory}
            if [[ -L "$controlDirectory" || ( -e "$controlDirectory" && ! -O "$controlDirectory" ) ]]; then
              echo "Refusing an unsafe SSH control directory: $controlDirectory" >&2
              exit 1
            fi
            run ${pkgs.coreutils}/bin/install -d -m 0700 -- "$controlDirectory"
          '';
        };
    })
  ];
}
