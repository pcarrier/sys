{
  blit,
  lib,
  config,
  ...
}:
let
  normalUsers = builtins.attrNames (
    lib.filterAttrs (_: u: (u.isNormalUser or false)) config.users.users
  );
in
{
  imports = [ blit.nixosModules.blit ];

  services.blit = {
    enable = true;
    audio.enable = true;
    users = normalUsers;
    shares.pcarrier = {
      user = "pcarrier";
      passFile = "/etc/blit.env";
      verboseWebrtc = true;
    };
  };
}
