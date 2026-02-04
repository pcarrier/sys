{ lib }:
lib.wsl {
  name = "dog";
  system = "x86_64-linux";
  emulated = [ "aarch64-linux" ];
  extraModules = [
    ../feat/docker.nix
    ../feat/flatpak.nix
  ];
} (lib.commonInputs // { nixos-wsl = lib.nixos-wsl; })
