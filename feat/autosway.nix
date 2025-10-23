{ pkgs, ... }:
{
  programs.sway.enable = true;
  services.greetd = {
    enable = true;
    settings = rec {
      initial_session = {
        command = "sway";
        user = "pcarrier";
      };
      default_session = initial_session;
    };
  };
}
