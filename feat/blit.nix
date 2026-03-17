{ blit, system, ... }:
let
  pkg = blit.packages.${system}.default;
in
{
  systemd.services.blit-gateway = {
    description = "blit gateway";
    after = [ "network.target" "blit-server.service" ];
    requires = [ "blit-server.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${pkg}/bin/blit-gateway";
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
      ExecStart = "${pkg}/bin/blit-server";
      Restart = "on-failure";
      User = "pcarrier";
      WorkingDirectory = "~";
      EnvironmentFile = "/etc/blit.env";
    };
  };
}
