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
    proxied = {
      url = "git+https://github.com/xmit-co/proxied.git?lfs=1";
      inputs = {
        flake-utils.follows = "flake-utils";
        nixpkgs.follows = "nixpkgs";
      };
    };
  };

  nixConfig = {
    extra-substituters = [ "https://pcarrier.cachix.org" ];
    extra-trusted-public-keys = [
      "pcarrier.cachix.org-1:7uUV/fKw5Byvc6KV6PsB8NJI6oDOO5JIFUV/B3LyR1s="
    ];
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
      proxied,
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
                proxied
                ;
            };
        cobb =
          build.bare
            {
              name = "cobb";
              system = "x86_64-linux";
              hardware = ./hw/amd1.nix;
              extraModules = [
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
                proxied
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
                proxied
                ;
            };
        kitten =
          build.bare
            {
              name = "kitten";
              system = "aarch64-linux";
              trusted = true;
              desktop = true;
              hardware = ./hw/macvm.nix;
              extraModules = [
                ./feat/autoniri.nix
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
                proxied
                ;
            };
        lynx =
          build.bare
            {
              name = "lynx";
              system = "x86_64-linux";
              trusted = true;
              desktop = true;
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/fw.nix;
              extraModules = [
                ./feat/autosway.nix
                ./feat/print.nix
                ./feat/docker.nix
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
                proxied
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
                proxied
                ;
            };
        hare =
          build.bare
            {
              name = "hare";
              system = "x86_64-linux";
              hardware = ./hw/nas1.nix;
              extraModules = [
                ./feat/zfs.nix
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
                proxied
                ;
            };
        hound =
          build.bare
            {
              name = "hound";
              trusted = true;
              desktop = true;
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/tower.nix;
              extraModules = [
                ./feat/autoniri.nix
                ./feat/print.nix
                ./feat/docker.nix
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
                proxied
                jovian
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
                ./folks/bkolobara.nix
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
                proxied
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
                ./folks/bkolobara.nix
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
                proxied
                ;
            };
        rabbit =
          build.bare
            {
              name = "rabbit";
              system = "x86_64-linux";
              emulated = [ "aarch64-linux" ];
              hardware = ./hw/zfw.nix;
              extraModules = [
                ./feat/zfs.nix
                ./feat/media.nix
                ./feat/proxied/ctrl.nix
                ./feat/proxied/dns.nix
                ./feat/proxied/proxying.nix
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
                proxied
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
                ./feat/autoniri.nix
                ./feat/print.nix
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
                proxied
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
