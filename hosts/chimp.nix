{ lib }:
lib.wsl {
  name = "chimp";
  system = "aarch64-linux";
  emulated = [ "x86_64-linux" ];
} (lib.commonInputs // { nixos-wsl = lib.nixos-wsl; })
