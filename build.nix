{
  wsl =
    {
      name,
      system,
      trusted ? true,
      extraModules ? [ ],
    }:
    {
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
          wsl.wslConf.network.hostname = name;
          _module.args = {
            systemType = "wsl";
            desktop = false;
            inherit trusted;
          };
        }
      ]
      ++ extraModules;
    };

  bare =
    {
      name,
      system,
      hardware,
      trusted ? false,
      desktop ? false,
      extraModules ? [ ],
    }:
    { nixpkgs, home-manager, ... }:
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
            inherit desktop trusted;
          };
        }
      ]
      ++ extraModules;
    };
}
