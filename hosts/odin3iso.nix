{ lib }:
lib.nixpkgs.lib.nixosSystem {
  system = "aarch64-linux";
  modules = [
    lib.determinate.nixosModules.default
    "${lib.nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
    ../hw/odin3.nix
    { boot.supportedFilesystems.zfs = lib.nixpkgs.lib.mkForce false; }
  ];
}
