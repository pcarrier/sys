{
  pkgs,
  nixpkgs-master,
  lib,
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
{
  nixpkgs.config = {
    allowUnfree = true;
    android_sdk.accept_license = true;
  };
  home-manager = {
    backupFileExtension = "backup";
    useGlobalPkgs = true;
    useUserPackages = true;
    users.pcarrier = lib.mkMerge (import ./home/config.nix {
      inherit
        pkgs
        nixpkgs-master
        lib
        system
        systemType
        baze
        tomorrowTheme
        plenty
        edl-ng
        trusted
        desktop
        ;
    });
  };
}
