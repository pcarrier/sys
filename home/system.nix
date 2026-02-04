{ pkgs }:
{
  wsl = {
    home = {
      packages = with pkgs; [ wslu ];
      sessionVariables = {
        BROWSER = "wslview";
        EDITOR = "nvim";
        VISUAL = "nvim";
      };
    };
  };
  bare = {
    home = {
      packages = with pkgs; [
        blueman
        brave
        brightnessctl
        coppwr
        drm_info
        firefox
        obs-studio
        lxqt.pavucontrol-qt
      ];
      sessionVariables = {
        BROWSER = "brave";
        EDITOR = "zeditor --wait";
        VISUAL = "zeditor --wait";
      };
    };
  };
}
