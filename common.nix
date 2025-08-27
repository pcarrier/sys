{
  pkgs,
  ...
}:
{
  security = {
    doas = {
      enable = true;
      wheelNeedsPassword = false;
    };
    sudo = {
      enable = true;
      wheelNeedsPassword = false;
    };
  };
  nix = {
    settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      trusted-users = [ "pcarrier" ];
    };
  };
  nixpkgs.config.allowUnfree = true;
  services.openssh.enable = true;
  programs.fish.enable = true;
  users.users.pcarrier = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    shell = pkgs.fish;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAvpKEUAeZFJkpIOuyV7PXuSkrNV51TCs7NxPCarRiEr"
    ];
  };
  system.stateVersion = "25.11";
}
