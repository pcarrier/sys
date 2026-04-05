{ pkgs, ... }:
{
  environment.systemPackages = [ pkgs.nvtopPackages.nvidia ];
  hardware = {
    graphics.enable = true;
    nvidia.open = true;
  };
  services.xserver.videoDrivers = [ "nvidia" ];
}
