{ inputs }:
let
  blit = inputs.blit;
  determinate = inputs.determinate;
  home-manager = inputs.home-manager;
  nix-darwin = inputs.nix-darwin;
  nix-homebrew = inputs.nix-homebrew;
  nixos-wsl = inputs.nixos-wsl;
  nixpkgs = inputs.nixpkgs;

  commonInputs = {
    inherit (inputs)
      nixpkgs
      nixpkgs-master
      home-manager
      tomorrowTheme
      baze
      blit
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
      specialArgs = moduleInputs // {
        inherit system trusted;
        systemType = "wsl";
        desktop = false;
      };
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
      specialArgs = moduleInputs // {
        inherit system trusted desktop;
        systemType = "bare";
      };
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
      specialArgs = moduleInputs // {
        inherit system trusted;
        systemType = "mac";
        desktop = false;
      };
      modules = [
        blit.darwinModules.blit
        nix-homebrew.darwinModules.nix-homebrew
        home-manager.darwinModules.home-manager
        ../home.nix
        {
          services.blit = {
            enable = true;
            gateways.default = {
              passFile = "/etc/blit.env";
              quic = true;
              storeConfig = true;
            };
          };
          nix.enable = false;
          nix-homebrew = {
            enable = true;
            user = "pcarrier";
            taps = {
              "indent-com/homebrew-tap" = inputs.homebrew-indent;
            };
          };
          nixpkgs.stdenv.hostPlatform = system;
          networking.hostName = name;
          programs = {
            _1password-gui.enable = true;
            fish.enable = true;
          };
          services = {
            openssh.enable = true;
            tailscale.enable = true;
          };
          system = {
            primaryUser = "pcarrier";
            stateVersion = 6;
          };
          users.users.pcarrier = {
            home = "/Users/pcarrier";
            shell = nixpkgs.legacyPackages.${system}.fish;
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
