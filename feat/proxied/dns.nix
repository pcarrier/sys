{ pkgs, proxied, ... }:
{
  networking.firewall = {
    allowedTCPPorts = [ 53 ];
    allowedUDPPorts = [ 53 ];
  };
  systemd.services.dns = {
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    environment = {
      DOMAIN = "proxied.eu";
      ADDR = "0.0.0.0:53";
      ROOT_ADDRS = "82.64.84.243";
      NS_HOSTS = "16al.pcarrier.com,16alb.pcarrier.com";
    };
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = "1s";
      ExecStart = "${proxied.packages.${pkgs.system}.dns}/bin/dns";
    };
  };
}
