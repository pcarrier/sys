{ blit, system, lib, config, ... }:
let
  normalUsers = builtins.attrNames (
    lib.filterAttrs (_: u: (u.isNormalUser or false)) config.users.users
  );
in
{
  imports = [ blit.nixosModules.blit ];

  services.blit = {
    enable = true;
    users = normalUsers;
    gateways.pcarrier = {
      user = "pcarrier";
      port = 3264;
      passFile = "/etc/blit.env";
    };
  };
}
