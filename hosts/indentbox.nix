{ lib }:
lib.ec2 {
  name = "indentbox";
  system = "aarch64-linux";
  extraModules = [
    ../feat/indentmoo.nix
    (
      { blit, ... }:
      {
        imports = [ blit.nixosModules.blit ];
        services = {
          blit = {
            enable = true;
            users = [ "pcarrier" ];
            gateways.pcarrier = {
              user = "pcarrier";
              port = 3264;
              passFile = "/etc/blit.env";
              storeConfig = true;
              webrtcProxy = true;
              quic = true;
            };
          };
          nginx.virtualHosts."blit.pierre.dev.indent.sh" = {
            enableACME = true;
            forceSSL = true;
            locations."/" = {
              proxyPass = "http://127.0.0.1:3264/";
              proxyWebsockets = true;
              extraConfig = ''
                proxy_buffering off;
                proxy_request_buffering off;
                tcp_nodelay on;
              '';
            };
          };
          tailscale.enable = true;
        };
        hardware.graphics.enable = true;

        networking.firewall.interfaces.tailscale0 = {
          allowedTCPPortRanges = [
            {
              from = 0;
              to = 65535;
            }
          ];
          allowedUDPPortRanges = [
            {
              from = 0;
              to = 65535;
            }
          ];
        };
      }
    )
  ];
} lib.commonInputs
