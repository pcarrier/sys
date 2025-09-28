{ pkgs, ... }:
{
  programs.zsh.enable = true;
  users.users.joao = {
    extraGroups = [ "wheel" ];
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
    home.packages = with pkgs; [
      codex
      claude-code
      ripgrep
      zellij
      jq
      lsof
      htop
      nload
      nil
      protols
      llvmPackages.clang-tools
      ghostty
      kitty
    ];
    services.lorri.enable = true;
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
      neovim = {
        enable = true;
        vimAlias = true;
      };
      git = {
        enable = true;
        package = pkgs.gitFull;
        aliases = {
          co = "checkout";
          su = "status";
          c = "commit";
          cm = "commit -m";
          p = "push";
          po = "push origin";
          pom = "push origin main";
        };
        lfs.enable = true;
        extraConfig = {
          diff.tool = "vimdiff";
          core.editor = "vim";
        };
      };
    };
  };
}
