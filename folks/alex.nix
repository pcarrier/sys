{ pkgs, ... }:
{
  programs.zsh.enable = true;
  users.users.alex = {
    extraGroups = [ "wheel" ];
    isNormalUser = true;
    shell = pkgs.zsh;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPfWrnU8suh3hnLEkciZmQdsC4QnHqb+lTSoYozVxKKn"
    ];
  };
  home-manager.users.alex = {
    home = {
      username = "alex";
      homeDirectory = "/home/alex";
      stateVersion = "25.11";
    };
    programs = {
      zsh = {
        enable = true;
        autosuggestion.enable = true;
        syntaxHighlighting.enable = true;
        oh-my-zsh = {
          enable = true;
          theme = "robbyrussell";
          plugins = [
            "git"
            "sudo"
            "docker"
            "kubectl"
          ];
        };
      };
      direnv = {
        enable = true;
        enableZshIntegration = true;
      };
      git = {
        enable = true;
        package = pkgs.gitFull;
        lfs.enable = true;
      };
    };
  };
}
