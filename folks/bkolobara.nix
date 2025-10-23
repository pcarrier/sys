{ pkgs, ... }:
{
  users.users.bkolobara = {
    extraGroups = [ "wheel" ];
    isNormalUser = true;
    shell = pkgs.bash;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO8aDdndkLlknL5esfEru5U02/4pFwWdUJpMUbfbG1OB"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJVElbYFfxFRGdEzW+K1jISiw80LLq9PviBfdujZ+YwO"
    ];
  };
  home-manager.users.bkolobara = {
    home = {
      username = "bkolobara";
      homeDirectory = "/home/bkolobara";
      stateVersion = "25.11";
    };
    programs = {
      direnv = {
        enable = true;
        enableBashIntegration = true;
      };
      git = {
        enable = true;
        package = pkgs.gitFull;
        lfs.enable = true;
      };
    };
  };
}
