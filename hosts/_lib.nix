{ inputs }:
let
  determinate = inputs.determinate;
  home-manager = inputs.home-manager;
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
in
{
  inherit wsl bare commonInputs;
  inherit (inputs) jovian nixos-wsl determinate nixpkgs;
}
