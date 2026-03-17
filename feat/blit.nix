{ blit, system, ... }:
{
  systemd.services.blit = {
    description = "blit";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${blit.packages.${system}.default}/bin/blit-server";
      Restart = "on-failure";
      User = "pcarrier";
      WorkingDirectory = "~";
      EnvironmentFile = "/etc/blit.env";
    };
  };
}
