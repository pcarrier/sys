{ pkgs, proxied, ... }:
{
  networking.firewall = {
    allowedTCPPorts = [ 123 ];
    allowedUDPPorts = [ 123 ];
  };
  systemd.services.proxying = {
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    environment.PORT = "123";
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = "1s";
      ExecStart = "${proxied.packages.${pkgs.stdenv.hostPlatform.system}.proxying}/bin/proxying";
    };
  };
}
