{
  pkgs,
  lib,
  trusted,
}:
lib.mkIf trusted {
  home.packages = with pkgs; [
    _1password-gui
    element-desktop
    signal-desktop
  ];
  programs = {
    keychain = {
      enable = true;
      enableFishIntegration = true;
      keys = [ "id_ed25519" ];
    };
    git.signing = {
      format = "ssh";
      key = "~/.ssh/id_ed25519";
      signByDefault = true;
    };
    gpg.enable = true;
  };
}
