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
  time.timeZone = "UTC";
  nix = {
    settings = {
      builders-use-substitutes = true;
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      trusted-users = [ "@wheel" ];
    };
    distributedBuilds = true;
    buildMachines = [
      {
        hostName = "gorilla";
        systems = [
          "x86_64-linux"
          "aarch64-linux"
        ];
        protocol = "ssh-ng";
        maxJobs = 16;
        speedFactor = 1;
        supportedFeatures = [
          "nixos-test"
          "benchmark"
          "big-parallel"
          "kvm"
        ];
      }
      # {
      #   hostName = "dog";
      #   systems = [
      #     "x86_64-linux"
      #     "aarch64-linux"
      #   ];
      #   protocol = "ssh-ng";
      #   maxJobs = 16;
      #   speedFactor = 2;
      #   supportedFeatures = [
      #     "nixos-test"
      #     "benchmark"
      #     "big-parallel"
      #     "kvm"
      #   ];
      # }
    ];
  };
  nixpkgs.config.allowUnfree = true;
  services = {
    opensmtpd = {
      enable = true;
      serverConfiguration = ''
        listen on localhost
        action "local" maildir alias <aliases>
        match for local action "local"
      '';
    };
    openssh = {
      enable = true;
      settings.PasswordAuthentication = false;
    };
  };
  programs.fish.enable = true;
  users.users.pcarrier = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    shell = pkgs.fish;
    openssh.authorizedKeys.keys = keys;
  };
  users.users.root.openssh.authorizedKeys.keys = keys;
  system.stateVersion = "25.11";
}
