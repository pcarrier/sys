{ lib }:
lib.bare {
  name = "rabbit";
  system = "x86_64-linux";
  emulated = [ "aarch64-linux" ];
  hardware = ../hw/zfw.nix;
  extraModules = [
    ../feat/flatpak.nix
    ../feat/libk.nix
    ../feat/mail.nix
    ../feat/media.nix
    ../feat/plentys.nix
    ../feat/proxied/ctrl.nix
    ../feat/proxied/dns.nix
    ../feat/proxied/proxying.nix
    ../feat/zfs.nix
  ];
} lib.commonInputs
