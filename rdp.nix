{ pkgs, ... }:
{
  services = {
    desktopManager.plasma6.enable = true;
    xrdp = {
      enable = true;
      defaultWindowManager = "startplasma-x11";
    };
  };
}
