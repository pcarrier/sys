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
      # The i0-i2 cluster's sandboxes workers (mesh 10.77.0.1-3, wg-ultimator)
      # run a docker CLI per tool call, preview relay and YAS uplink, each an
      # SSH connection unless multiplexed: bursts of hundreds a minute made the
      # default MaxStartups 10:30:100 drop them ("kex_exchange_identification:
      # Connection reset by peer"). Admit more unauthenticated connections, never
      # penalise the mesh peers, and let their multiplexed masters carry many
      # channels.
      services.openssh.settings = {
        MaxStartups = "100:30:300";
        PerSourcePenaltyExemptList = "10.77.0.1/32,10.77.0.2/32,10.77.0.3/32";
      };
      services.openssh.extraConfig = lib.mkAfter ''
        Match User pcarrier Address 100.110.157.123
          MaxSessions 128
        Match User pcarrier Address 10.77.0.1,10.77.0.2,10.77.0.3
          MaxSessions 512
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
