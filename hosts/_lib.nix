{ inputs }:
let
  determinate = inputs.determinate;
  home-manager = inputs.home-manager;
  nix-darwin = inputs.nix-darwin;
  nixos-wsl = inputs.nixos-wsl;
  nixpkgs = inputs.nixpkgs;
  yas = inputs.yas;

  commonInputs = {
    inherit (inputs)
      nixpkgs
      nixpkgs-master
      home-manager
      tomorrowTheme
      baze
      yas
      plenty
      edl-ng
      claude-desktop
      codex-desktop
      determinate
      moo
      kimi-code
      nix-vscode-extensions
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
          nixpkgs.hostPlatform.system = system;
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
          nixpkgs.hostPlatform.system = system;
          boot.binfmt.emulatedSystems = emulated;
          networking.hostName = name;
        }
      ]
      ++ extraModules;
    };

  ec2 =
    {
      name,
      system,
      emulated ? [ ],
      trusted ? false,
      extraModules ? [ ],
    }:
    moduleInputs:
    nixpkgs.lib.nixosSystem {
      specialArgs = moduleInputs // {
        inherit system trusted;
        systemType = "bare";
        desktop = false;
      };
      modules = [
        determinate.nixosModules.default
        home-manager.nixosModules.home-manager
        ../base/common.nix
        ../home.nix
        (
          { modulesPath, ... }:
          {
            imports = [ "${modulesPath}/virtualisation/amazon-image.nix" ];
            ec2.efi = true;
          }
        )
        {
          nixpkgs.hostPlatform.system = system;
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
        yas.darwinModules.yas
        home-manager.darwinModules.home-manager
        ../home.nix
        {
          services.yas = {
            enable = true;
            # The server serves the browser itself; there is no separate edge
            # agent to configure.
            edge = {
              enable = true;
              passFile = "/etc/yas.env";
            };
          };
          nix.enable = false;
          nixpkgs.hostPlatform.system = system;
          networking.hostName = name;
          programs.fish.enable = true;
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
  inherit
    wsl
    bare
    ec2
    darwin
    commonInputs
    ;
  inherit (inputs)
    jovian
    nixos-wsl
    nix-darwin
    determinate
    nixpkgs
    yas
    ;
}
