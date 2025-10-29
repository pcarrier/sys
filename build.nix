{
  wsl =
    {
      name,
      system,
      emulated ? [ ],
      trusted ? true,
      extraModules ? [ ],
    }:
    inputs@{
      nixpkgs,
      nixos-wsl,
      home-manager,
      ...
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
          boot.binfmt.emulatedSystems = emulated;
          wsl.wslConf.network.hostname = name;
          _module.args = inputs // {
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
    inputs@{
      nixpkgs,
      home-manager,
      ...
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
          boot.binfmt.emulatedSystems = emulated;
          networking.hostName = name;
          _module.args = inputs // {
            inherit system trusted desktop;
            systemType = "bare";
          };
        }
      ]
      ++ extraModules;
    };
}
