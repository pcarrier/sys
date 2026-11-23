{ ... }:
{
  users.users.dauriac = {
    extraGroups = [ "video" ];
    isNormalUser = true;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKrCJrDJCSNlyH4EfcDAnhhbrZE7Rv9ODeM37kKhs5BY"
    ];
  };
}
