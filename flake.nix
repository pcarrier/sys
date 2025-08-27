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
      makeWsl =
        {
          name,
          system,
          trusted ? true,
        }:
        nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            nixos-wsl.nixosModules.default
            home-manager.nixosModules.home-manager
            ./common.nix
            ./wsl.nix
            ./home.nix
            {
              wsl.wslConf.network.hostname = name;
              _module.args = {
                systemType = "wsl";
                inherit trusted;
              };
            }
          ];
        };

      makeBare =
        {
          name,
          system,
          hardware,
          trusted ? false,
          extraModules ? [ ],
        }:
        nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            home-manager.nixosModules.home-manager
            ./common.nix
            ./bare.nix
            hardware
            ./home.nix
            {
              networking.hostName = name;
              _module.args = {
                systemType = "bare";
                inherit trusted;
              };
            }
          ]
          ++ extraModules;
        };
    in
    {
      nixosConfigurations = {
        chimp = makeWsl {
          name = "chimp";
          system = "aarch64-linux";
        };
        dog = makeWsl {
          name = "dog";
          system = "x86_64-linux";
        };
        gorilla = makeBare {
          name = "gorilla";
          system = "x86_64-linux";
          hardware = ./hw/ax52.nix;
          extraModules = [
            ./docker.nix
            ./rdp.nix
          ];
        };
      };
    };
}
