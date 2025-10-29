{ system, plenty, ... }:
{
  environment.systemPackages = [ plenty.packages.${system}.plentys ];
}
