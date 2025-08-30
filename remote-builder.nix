{
  nix = {
    settings = {
      builders-use-substitutes = true;
    };
    distributedBuilds = true;
    buildMachines = [
      {
        hostName = "gorilla";
        system = "x86_64-linux";
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
      {
        hostName = "dog";
        system = "x86_64-linux";
        protocol = "ssh-ng";
        maxJobs = 16;
        speedFactor = 2;
        supportedFeatures = [
          "nixos-test"
          "benchmark"
          "big-parallel"
          "kvm"
        ];
      }
    ];
  };
}
