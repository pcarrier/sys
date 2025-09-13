{ lib, config, ... }:
let
  normalUsers = builtins.attrNames (
    lib.filterAttrs (_: u: (u.isNormalUser or false)) config.users.users
  );
in
{
  virtualisation.docker = {
    enable = true;
    extraOptions = "--insecure-registry 10.42.42.42:5000";
  };

  # Add all normal users to the docker group without redefining users.users
  users.groups.docker.members = normalUsers;
}
