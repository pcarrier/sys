{ pkgs, ... }:
{
  programs.niri = {
    enable = true;
    package = pkgs.niri.overrideAttrs (oldAttrs: rec {
      src = pkgs.fetchFromGitHub {
        owner = "pcarrier";
        repo = "niri";
        rev = "step1";
        sha256 = "sha256-WCcc31LGw52D4CrJjXJnmjfYAF68sRwwv6PQKUGyjt4=";
      };
      cargoDeps = pkgs.rustPlatform.fetchCargoVendor {
        inherit src;
        hash = "sha256-3A37vUNv37IKAm9MdlfVMkuTd/HZSkPO+gv1m23qJvo=";
      };
    });
  };
  services.greetd = {
      enable = true;
      settings = rec {
        initial_session = {
          command = "niri-session";
          user = "pcarrier";
        };
        default_session = initial_session;
      };
  };
}
