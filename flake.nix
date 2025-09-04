{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager";
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
    colmena = {
      url = "github:zhaofengli/colmena";
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
      colmena,
      nixpkgs,
      nixos-wsl,
      home-manager,
      baze,
      jovian,
      ...
    }:
    let
      build = import ./build.nix;
    in
    {
      colmenaHive = colmena.lib.makeHive self.outputs.colmena;
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
                nixos-wsl
                home-manager
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
                nixos-wsl
                home-manager
                baze
                ;
            };
        gorilla = build.bare {
          name = "gorilla";
          system = "x86_64-linux";
          emulated = [ "aarch64-linux" ];
          hardware = ./hw/ax52.nix;
          extraModules = [
            ./feat/docker.nix
            ./feat/kube.nix
            ./feat/rdp.nix
          ];
        } { inherit nixpkgs home-manager baze; };
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
                home-manager
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
