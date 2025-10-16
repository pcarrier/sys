{
  nix = {
    distributedBuilds = true;
    settings = {
      builders-use-substitutes = true;
      max-jobs = 0;
    };
    buildMachines = [
      {
        hostName = "monster";
        sshUser = "pcarrier";
        sshKey = "/root/.ssh/id_ed25519";
        systems = [ "x86_64-linux" "aarch64-linux" ];
        protocol = "ssh-ng";
        maxJobs = 64;
        speedFactor = 100;
        supportedFeatures = [ "big-parallel" "kvm" ];
      }
    ];
  };
}
