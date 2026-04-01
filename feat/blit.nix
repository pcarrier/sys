{ blit, system, lib, config, ... }:
let
  normalUsers = builtins.attrNames (
    lib.filterAttrs (_: u: (u.isNormalUser or false)) config.users.users
  );
in
{
  imports = [ blit.nixosModules.blit ];

  networking = {
    firewall.allowedUDPPorts = [ 443 3264 ];
    nftables = {
      enable = true;
      tables.blit-redirect = {
        family = "inet";
        content = ''
          chain prerouting {
            type nat hook prerouting priority dstnat; policy accept;
            udp dport 443 redirect to :3264
          }
        '';
      };
    };
  };

  services.blit = {
    enable = true;
    users = normalUsers;
    gateways.pcarrier = {
      user = "pcarrier";
      port = 3264;
      passFile = "/etc/blit.env";
      quic = true;
      storeConfig = true;
    };
  };
}
