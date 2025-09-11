{ pkgs, ... }:
{
  programs.zsh.enable = true;
  users.users.joao = {
    isNormalUser = true;
    shell = pkgs.zsh;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPp6RaUoHeQW6u5n4XY/ynpdi2aBgfTUM9MXi6eXTW0j"
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIH362KYNpD9IH8PZ7++e2+XxhWBfu79vFNmugntce6dX"
    ];
  };
  home-manager.users.joao = {
    home = {
      username = "joao";
      homeDirectory = "/home/joao";
      stateVersion = "25.11";
    };
    programs = {
      zsh = {
        enable = true;
        enableCompletion = true;
        syntaxHighlighting.enable = true;
      };
      direnv = {
        enable = true;
        enableZshIntegration = true;
      };
      git = {
        enable = true;
      };
    };
  };
}
