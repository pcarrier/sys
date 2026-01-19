{ pkgs, proxied, ... }:
{
  networking.firewall.allowedTCPPorts = [ 443 ];
  services = {
    redis.servers.r.enable = true;
    nginx = {
      enable = true;
      virtualHosts = {
        "proxied.eu" = {
          enableACME = true;
          forceSSL = true;
          locations."/".proxyPass = "http://127.0.0.1:8080";
        };
      };
    };
  };
  systemd.services.ctrl = {
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = "1s";
      ExecStart = "${proxied.packages.${pkgs.stdenv.hostPlatform.system}.ctrl}/bin/ctrl";
    };
  };
}
