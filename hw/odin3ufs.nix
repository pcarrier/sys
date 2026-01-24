{
  imports = [ ./odin3.nix ];

  fileSystems = {
    "/" = {
      device = "/dev/disk/by-label/root";
      fsType = "f2fs";
      options = [
        "noatime"
        "compress_algorithm=zstd"
        "atgc"
        "gc_merge"
      ];
    };
    "/boot" = {
      device = "/dev/disk/by-label/boot";
      fsType = "vfat";
      options = [
        "fmask=0022"
        "dmask=0022"
      ];
    };
  };
}
