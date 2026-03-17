{ btrm, system, ... }:
{
  systemd.services.btrm = {
    description = "btrm";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${btrm.packages.${system}.default}/bin/btrm-server";
      Restart = "on-failure";
      User = "pcarrier";
      WorkingDirectory = "~";
      EnvironmentFile = "/etc/btrm.env";
    };
  };
}
