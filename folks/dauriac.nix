{ pkgs, ... }:
{
  users.users.dauriac = {
    extraGroups = [ "wheel" ];
    isNormalUser = true;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIESswVXkWceiXMo5jasnYmopKXKVmhdK/xb1wrx6vFkA"
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
