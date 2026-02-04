{ lib }:
lib.bare {
  name = "hound";
  trusted = true;
  desktop = true;
  system = "x86_64-linux";
  emulated = [ "aarch64-linux" ];
  hardware = ../hw/tower.nix;
  extraModules = [
    ../feat/autoniri.nix
    ../feat/docker.nix
    ../feat/flatpak.nix
    ../feat/mediaclient.nix
    ../feat/ollama.nix
    ../feat/plugdev.nix
    ../feat/print.nix
    ../feat/steam.nix
    ../feat/vbox.nix
    ../folks/dauriac.nix
  ];
} (lib.commonInputs // { inherit (lib) jovian; })
