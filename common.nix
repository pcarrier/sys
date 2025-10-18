{
  pkgs,
  ...
}:
let
  keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAvpKEUAeZFJkpIOuyV7PXuSkrNV51TCs7NxPCarRiEr"
  ];
in
{
  documentation.nixos.enable = false;
  hardware.enableRedistributableFirmware = true;
  zramSwap.enable = true;
  security = {
    acme = {
      acceptTerms = true;
      defaults.email = "pc@rrier.fr";
    };
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
      builders-use-substitutes = true;
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      trusted-users = [ "@wheel" ];
    };
  };
  nixpkgs.config = {
    allowUnfree = true;
    allowBroken = true;
    allowUnsupportedSystem = true;
  };
  services = {
    sysstat.enable = true;
    opensmtpd = {
      enable = true;
      serverConfiguration = ''
        listen on localhost
        action "local" maildir
        match for local action "local"
      '';
    };
    openssh = {
      enable = true;
      settings.PasswordAuthentication = false;
    };
  };
  programs = {
    fish.enable = true;
    nix-ld.enable = true;
  };
  users.users.pcarrier = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    shell = pkgs.fish;
    openssh.authorizedKeys.keys = keys;
  };
  users.users.root.openssh.authorizedKeys.keys = keys;
  system.stateVersion = "25.11";
}
