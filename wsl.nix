{ config, lib, pkgs, ... }: {
  wsl = {
    enable = true;
    interop.register = true;
    startMenuLaunchers = true;
    useWindowsDriver = true;
    wrapBinSh = true;
    extraBin = [{
      name = "bash";
      src = config.wsl.binShExe;
    }];
    defaultUser = "pcarrier";
  };

  security = {
    doas = {
      enable = true;
      wheelNeedsPassword = false;
    };
  };

  nix = {
    settings = {
      experimental-features = [ "nix-command" "flakes" ];
      trusted-users = [ "pcarrier" ];
    };
  };

  nixpkgs.config.allowUnfree = true;

  services.sshd.enable = true;

  programs = {
    nix-ld.enable = true;
    fish.enable = true;
  };

  users.users.pcarrier = {
    shell = pkgs.fish;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAvpKEUAeZFJkpIOuyV7PXuSkrNV51TCs7NxPCarRiEr"
    ];
  };

  system.stateVersion = "25.11";
}
