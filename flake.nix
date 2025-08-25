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

  outputs = { self, nixpkgs, nixos-wsl, home-manager, ... }:
    let
      makeWsl = { name, system }:
        nixpkgs.lib.nixosSystem {
          system = system;
          modules = [
            nixos-wsl.nixosModules.default
            home-manager.nixosModules.home-manager
            ./wsl.nix
            ./home.nix
            { wsl.wslConf.network.hostname = name; }
          ];
        };
    in {
      nixosConfigurations = {
        chimp = makeWsl {
          name = "chimp";
          system = "aarch64-linux";
        };
        dog = makeWsl {
          name = "dog";
          system = "x86_64-linux";
        };
      };
    };
}
