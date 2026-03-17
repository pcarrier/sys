{ btrm, system, ... }:
{
  users.users.btrm = {
    isSystemUser = true;
    group = "btrm";
  };
  users.groups.btrm = { };
  systemd.services.btrm = {
    description = "btrm";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${btrm.packages.${system}.default}/bin/btrm";
      Restart = "on-failure";
      User = "btrm";
      Group = "btrm";
      EnvironmentFile = "/etc/btrm.env";
    };
  };
}
