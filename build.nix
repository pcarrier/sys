{
  wsl =
    {
      name,
      system,
      emulated ? [ ],
      trusted ? true,
      extraModules ? [ ],
    }:
    {
      nixpkgs,
      nixos-wsl,
      home-manager,
      baze,
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
          _module.args = {
            systemType = "wsl";
            desktop = false;
            inherit system trusted baze;
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
    {
      nixpkgs,
      home-manager,
      baze,
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
          _module.args = {
            systemType = "bare";
            inherit system desktop trusted baze;
          };
        }
      ]
      ++ extraModules;
    };
}
