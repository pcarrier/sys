{ pkgs, ... }:
{
  xdg.portal = {
    enable = true;
    extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
    config.common.default = "*";
  };
  services.flatpak.enable = true;
  environment.systemPackages = [ pkgs.flatpak pkgs.flatpak-builder ];
}
