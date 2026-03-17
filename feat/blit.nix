{ blit, system, ... }:
let
  gw = blit.packages.${system}.blit-gateway;
  server = blit.packages.${system}.blit-server;
in
{
  systemd.services.blit-gateway = {
    description = "blit gateway";
    after = [ "network.target" "blit-server.service" ];
    requires = [ "blit-server.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${gw}/bin/blit-gateway";
      Restart = "on-failure";
      User = "pcarrier";
      EnvironmentFile = "/etc/blit-gateway.env";
    };
  };

  systemd.services.blit-server = {
    description = "blit server";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${server}/bin/blit-server";
      Restart = "on-failure";
      User = "pcarrier";
      WorkingDirectory = "~";
      EnvironmentFile = "/etc/blit.env";
    };
  };
}
