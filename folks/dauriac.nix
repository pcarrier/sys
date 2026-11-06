{ pkgs, ... }:
{
  users.users.dauriac = {
    extraGroups = [ "video" ];
    isNormalUser = true;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKrCJrDJCSNlyH4EfcDAnhhbrZE7Rv9ODeM37kKhs5BY"
    ];
  };
  home-manager.users.dauriac = {
    manual.manpages.enable = false;
    home = {
      username = "dauriac";
      homeDirectory = "/home/dauriac";
      stateVersion = "26.11";
    };
    programs = {
      git = {
        enable = true;
        package = pkgs.gitFull;
        lfs.enable = true;
      };
    };
  };
}
