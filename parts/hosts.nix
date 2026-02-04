{ inputs, ... }:
let
  lib = import ../hosts/_lib.nix { inherit inputs; };
in
{
  flake.nixosConfigurations = {
    amoeba = import ../hosts/amoeba.nix { inherit lib; };
    odin3iso = import ../hosts/odin3iso.nix { inherit lib; };
    chimp = import ../hosts/chimp.nix { inherit lib; };
    dog = import ../hosts/dog.nix { inherit lib; };
    kitten = import ../hosts/kitten.nix { inherit lib; };
    hare = import ../hosts/hare.nix { inherit lib; };
    hound = import ../hosts/hound.nix { inherit lib; };
    rabbit = import ../hosts/rabbit.nix { inherit lib; };
    sloth = import ../hosts/sloth.nix { inherit lib; };
  };
}
