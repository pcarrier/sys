{
  wsl =
    {
      name,
      system,
      trusted ? true,
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
            inherit trusted;
          };
        }
      ];
    };

  bare =
    {
      name,
      system,
      hardware,
      trusted ? false,
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
            inherit trusted;
          };
        }
      ]
      ++ extraModules;
    };
}
