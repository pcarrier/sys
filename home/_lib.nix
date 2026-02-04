{
  pkgs,
  nixpkgs-master,
  system,
}:
let
  pkgs-master = import nixpkgs-master {
    inherit system;
    config.allowUnfree = true;
  };

  clip = pkgs.stdenv.mkDerivation {
    name = "clip";
    src = pkgs.fetchurl {
      url = "https://raw.githubusercontent.com/sentriz/cliphist/refs/heads/master/contrib/cliphist-fuzzel-img";
      sha256 = "sha256-NgQ87yZCusF/FYprJJ+fvkA3VdrvHp4LyylQ0ajBvjU=";
    };
    phases = [ "installPhase" ];
    installPhase = ''
      install -Dm755 $src $out/bin/clip
    '';
  };

  gitPackage = pkgs.gitFull;
in
{
  inherit pkgs-master clip gitPackage;
}
