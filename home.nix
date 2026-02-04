{
  pkgs,
  nixpkgs-master,
  lib,
  nix-index,
  system,
  systemType,
  baze,
  tomorrowTheme,
  plenty,
  edl-ng,
  trusted ? false,
  desktop ? false,
  ...
}:
let
  homeLib = import ./home/_lib.nix { inherit pkgs nixpkgs-master system; };
  baseConfig = import ./home/base.nix { inherit pkgs system baze nix-index plenty; };
  systemConfigs = import ./home/system.nix { inherit pkgs; };
  desktopConfig = import ./home/desktop.nix { inherit pkgs lib homeLib system edl-ng desktop; };
  trustedConfig = import ./home/trusted.nix { inherit pkgs lib trusted; };
  programsConfig = import ./home/programs.nix { inherit pkgs homeLib tomorrowTheme; };
in
{
  home-manager = {
    backupFileExtension = "backup";
    useGlobalPkgs = true;
    useUserPackages = true;
    users = {
      pcarrier = lib.mkMerge [
        baseConfig
        (systemConfigs.${systemType} or { })
        desktopConfig
        trustedConfig
        programsConfig
      ];
    };
  };
}
