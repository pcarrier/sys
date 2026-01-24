{ lib, config, ... }:
let
  normalUsers = builtins.attrNames (
    lib.filterAttrs (_: u: (u.isNormalUser or false)) config.users.users
  );
in
{
  users.groups.plugdev.members = normalUsers;

  services.udev.extraRules = ''
    SUBSYSTEM=="usb", MODE="0660", GROUP="plugdev"
  '';
}
