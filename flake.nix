{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    nixpkgs-master.url = "github:NixOS/nixpkgs/master";
    flake-utils.url = "github:numtide/flake-utils";
    tomorrowTheme = {
      url = "github:chriskempson/tomorrow-theme";
      flake = false;
    };
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-index = {
      url = "github:nix-community/nix-index";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixos-wsl = {
      url = "github:nix-community/NixOS-WSL";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    jovian = {
      url = "github:Jovian-Experiments/Jovian-NixOS";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    baze = {
      url = "github:pcarrier/baze";
      inputs = {
        flake-utils.follows = "flake-utils";
        nixpkgs.follows = "nixpkgs";
      };
    };
  };

  outputs =
    {
      self,
      flake-utils,
      nixpkgs,
      nixpkgs-master,
      nix-index,
      nixos-wsl,
      home-manager,
      tomorrowTheme,
      baze,
      jovian,
      ...
    }:
    let
      build = import ./build.nix;
    in
    {
      nixosConfigurations = {
        chimp =
          build.wsl
            {
              name = "chimp";
              system = "aarch64-linux";
              emulated = [ "x86_64-linux" ];
              extraModules = [
                ./feat/mail.nix
                ./feat/docker.nix
              ];
            }
            {
              inherit
                nixpkgs
                nixpkgs-master
                nix-index
                nixos-wsl
                home-manager
                tomorrowTheme
                baze
                ;
            };
        dog =
          build.wsl
            {
              name = "dog";
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              extraModules = [
                ./feat/docker.nix
              ];
            }
            {
              inherit
                nixpkgs
                nixpkgs-master
                nix-index
                nixos-wsl
                home-manager
                tomorrowTheme
                baze
                ;
            };
        gorilla =
          build.bare
            {
              name = "gorilla";
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/amd1.nix;
              extraModules = [
                ./feat/docker.nix
                ./feat/rdp.nix
              ];
            }
            {
              inherit
                nixpkgs
                nixpkgs-master
                nix-index
                home-manager
                tomorrowTheme
                baze
                ;
            };
        komodo =
          build.bare
            {
              name = "komodo";
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/nv1.nix;
              extraModules = [
                ./feat/docker.nix
                ./feat/rdp.nix
                ./folks/joao.nix
              ];
            }
            {
              inherit
                nixpkgs
                nixpkgs-master
                nix-index
                home-manager
                tomorrowTheme
                baze
                ;
            };
        monster =
          build.bare
            {
              name = "monster";
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/amd1.nix;
              extraModules = [
                ./feat/docker.nix
                ./folks/joao.nix
                ./folks/alex.nix
              ];
            }
            {
              inherit
                nixpkgs
                nixpkgs-master
                nix-index
                home-manager
                tomorrowTheme
                baze
                ;
            };
        rabbit =
          build.bare
            {
              name = "rabbit";
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/fw.nix;
              extraModules = [
                ./feat/zfs.nix
                ./feat/media.nix
                {
                  boot.zfs.extraPools = [
                    "tank"
                    "tonk"
                  ];
                  networking.hostId = "12345678";
                  services = {
                    syncoid = {
                      enable = true;
                      commands.tank-to-tonk = {
                        source = "tank";
                        target = "tonk/backups/tank";
                      };
                    };
                    sanoid = {
                      enable = true;
                      templates = {
                        perso = {
                          hourly = 48;
                          daily = 30;
                          weekly = 5;
                          monthly = 12;
                          yearly = 10;
                          autosnap = true;
                          autoprune = true;
                        };
                      };
                      datasets = {
                        "tank" = {
                          useTemplate = [ "perso" ];
                        };
                        "tonk" = {
                          useTemplate = [ "perso" ];
                        };
                      };
                    };
                  };
                }
              ];
            }
            {
              inherit
                nixpkgs
                nixpkgs-master
                nix-index
                home-manager
                tomorrowTheme
                baze
                ;
            };
        sloth =
          build.bare
            {
              name = "sloth";
              trusted = true;
              desktop = true;
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/deck.nix;
              extraModules = [
                jovian.nixosModules.default
              ];
            }
            {
              inherit
                nixpkgs
                nixpkgs-master
                nix-index
                home-manager
                tomorrowTheme
                baze
                jovian
                ;
            };
      };
    }
    // flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells = {
          default = pkgs.mkShell {
            packages = with pkgs; [
              nixfmt
              (pkgs.writeShellScriptBin "hosts" ''
                echo ${builtins.toString (builtins.attrNames self.nixosConfigurations)}
              '')
            ];
          };
        };
      }
    );
}
