{
  pkgs,
  nixpkgs-master,
  lib,
  system,
  systemType,
  baze,
  tomorrowTheme,
  plenty,
  edl-ng,
  trusted ? false,
  desktop ? false,
}:
[
  (import ./base.nix { inherit pkgs lib system systemType baze plenty; })
  (import ./desktop.nix { inherit pkgs lib system edl-ng desktop; })
  (import ./programs.nix { inherit pkgs lib nixpkgs-master system tomorrowTheme trusted; })
]
