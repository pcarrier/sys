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
      nixpkgs-master,
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
            inherit
              system
              trusted
              baze
              nix-index
              nixpkgs-master
              tomorrowTheme
              ;
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
      nixpkgs-master,
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
              system
              desktop
              trusted
              baze
              nix-index
              nixpkgs-master
              tomorrowTheme
              ;
          };
        }
      ]
      ++ extraModules;
    };
}
