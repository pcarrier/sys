{ lib, config, ... }:
let
  normalUsers = builtins.attrNames (
    lib.filterAttrs (_: u: (u.isNormalUser or false)) config.users.users
  );
in
{
  virtualisation.docker.enable = true;
  users.groups.docker.members = normalUsers;
}
