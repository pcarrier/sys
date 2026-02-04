{ lib }:
lib.bare {
  name = "sloth";
  trusted = true;
  desktop = true;
  system = "x86_64-linux";
  emulated = [ "aarch64-linux" ];
  hardware = ../hw/deck.nix;
  extraModules = [
    ../feat/autoniri.nix
    ../feat/print.nix
    lib.jovian.nixosModules.default
  ];
} (lib.commonInputs // { inherit (lib) jovian; })
