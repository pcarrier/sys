{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
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
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
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
      nixosConfigurations = {
        chimp =
          build.wsl
            {
              name = "chimp";
              system = "aarch64-linux";
              emulated = [ "x86_64-linux" ];
              extraModules = [
                ./mail.nix
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
                ./docker.nix
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
            ./docker.nix
            ./kube.nix
            ./rdp.nix
          ];
        } { inherit nixpkgs home-manager baze; };
        sloth =
          build.bare
            {
              name = "sloth";
              trusted = true;
              desktop = true;
              system = "x86_64-linux";
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
    };
}
