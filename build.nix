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
      nix-index,
      nixos-wsl,
      home-manager,
      tomorrowTheme,
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
            inherit trusted baze nix-index;
            tomorrowThemeSrc = tomorrowTheme;
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
      nix-index,
      home-manager,
      tomorrowTheme,
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
            inherit
              desktop
              trusted
              baze
              nix-index
              ;
            tomorrowThemeSrc = tomorrowTheme;
          };
        }
      ]
      ++ extraModules;
    };
}
