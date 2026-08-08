{
  pkgs,
  nixpkgs-master,
  lib,
  system,
  systemType,
  baze,
  blit,
  tomorrowTheme,
  plenty,
  edl-ng,
  claude-desktop,
  codex-desktop,
  kimi-code,
  nix-vscode-extensions,
  trusted ? false,
  desktop ? false,
  ...
}:
{
  nixpkgs = {
    config = {
      allowUnfree = true;
      android_sdk.accept_license = true;
    };
    overlays = [ nix-vscode-extensions.overlays.default ];
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
        blit
        tomorrowTheme
        plenty
        edl-ng
        claude-desktop
        codex-desktop
        kimi-code
        trusted
        desktop
        ;
    });
  };
}
