{ pkgs, ... }:
{
  hardware = {
    graphics.enable = true;
    nvidia.open = true;
  };
  services.xserver.videoDrivers = [ "nvidia" ];
  environment.systemPackages = [
    # cuda_compat is aarch64-only but allowUnsupportedSystem makes it look
    # available, breaking the build; drop it from the scope.
    (pkgs.nvtopPackages.nvidia.override {
      cudaPackages = pkgs.cudaPackages.overrideScope (_: _: {
        cuda_compat = null;
      });
    })
  ];
}
