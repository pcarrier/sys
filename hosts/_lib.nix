{ inputs }:
let
  determinate = inputs.determinate;
  home-manager = inputs.home-manager;
  nix-darwin = inputs.nix-darwin;
  nixos-wsl = inputs.nixos-wsl;
  nixpkgs = inputs.nixpkgs;

  commonInputs = {
    inherit (inputs)
      nixpkgs
      nixpkgs-master
      home-manager
      tomorrowTheme
      baze
      plenty
      edl-ng
      determinate
      ;
  };

  wsl =
    {
      name,
      system,
      emulated ? [ ],
      trusted ? true,
      extraModules ? [ ],
    }:
    moduleInputs:
    nixpkgs.lib.nixosSystem {
      inherit system;
      modules = [
        determinate.nixosModules.default
        nixos-wsl.nixosModules.default
        home-manager.nixosModules.home-manager
        ../base/common.nix
        ../base/wsl.nix
        ../home.nix
        {
          boot.binfmt.emulatedSystems = emulated;
          wsl.wslConf.network.hostname = name;
          _module.args = moduleInputs // {
            inherit system trusted;
            systemType = "wsl";
            desktop = false;
          };
        }
      ]
      ++ extraModules;
    };

  bare =
    {
      name,
      system,
      emulated ? [ ],
      hardware,
      trusted ? false,
      desktop ? false,
      extraModules ? [ ],
    }:
    moduleInputs:
    nixpkgs.lib.nixosSystem {
      inherit system;
      modules = [
        determinate.nixosModules.default
        home-manager.nixosModules.home-manager
        ../base/common.nix
        ../base/bare.nix
        hardware
        ../home.nix
        {
          boot.binfmt.emulatedSystems = emulated;
          networking.hostName = name;
          _module.args = moduleInputs // {
            inherit system trusted desktop;
            systemType = "bare";
          };
        }
      ]
      ++ extraModules;
    };

  darwin =
    {
      name,
      system,
      trusted ? true,
      extraModules ? [ ],
    }:
    moduleInputs:
    nix-darwin.lib.darwinSystem {
      inherit system;
      modules = [
        home-manager.darwinModules.home-manager
        ../home.nix
        {
          nix.enable = false;
          nixpkgs.hostPlatform = system;
          networking.hostName = name;
          programs._1password-gui.enable = true;
          programs.fish.enable = true;
          services.openssh.enable = true;
          system.primaryUser = "pcarrier";
          system.stateVersion = 6;
          users.users.pcarrier = {
            home = "/Users/pcarrier";
            shell = nixpkgs.legacyPackages.${system}.fish;
          };
          _module.args = moduleInputs // {
            inherit system trusted;
            systemType = "mac";
            desktop = false;
          };
        }
      ]
      ++ extraModules;
    };
in
{
  inherit wsl bare darwin commonInputs;
  inherit (inputs) jovian nixos-wsl nix-darwin determinate nixpkgs;
}
