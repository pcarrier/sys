{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixos-wsl = {
      url = "github:nix-community/NixOS-WSL";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      nixos-wsl,
      home-manager,
      ...
    }:
    let
      build = import ./build.nix;
    in
    {
      nixosConfigurations = {
        chimp = build.wsl {
          name = "chimp";
          system = "aarch64-linux";
        } { inherit nixpkgs nixos-wsl home-manager; };
        dog = build.wsl {
          name = "dog";
          system = "x86_64-linux";
        } { inherit nixpkgs nixos-wsl home-manager; };
        gorilla = build.bare {
          name = "gorilla";
          system = "x86_64-linux";
          hardware = ./hw/ax52.nix;
          extraModules = [
            ./docker.nix
            ./rdp.nix
          ];
        } { inherit nixpkgs home-manager; };
      };
    };
}
