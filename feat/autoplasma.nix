{ pkgs, ... }:
{
  services = {
    desktopManager.plasma6.enable = true;
    greetd = {
      enable = true;
      settings = rec {
        initial_session = {
          command = "startplasma-wayland";
          user = "pcarrier";
        };
        default_session = initial_session;
      };
    };
  };
}
