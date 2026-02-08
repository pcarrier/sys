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
  # zramSwap.enable = true;
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
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    builders-use-substitutes = true;
    trusted-users = [ "@wheel" ];
    extra-substituters = [ "https://install.determinate.systems" ];
    extra-trusted-public-keys = [ "install.determinate.systems:ywrWBLviPMM0t4GBWfY8XFoQ1EYzp2vL44cBnN+dXOM=" ];
  };
  nixpkgs.config = {
    allowUnfree = true;
    allowBroken = true;
    allowUnsupportedSystem = true;
    android_sdk.accept_license = true;
  };
  services = {
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
