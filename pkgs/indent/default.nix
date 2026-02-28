{ pkgs }:
let
  version = "0.1.51";
  wheels = pkgs.stdenv.mkDerivation {
    pname = "indent-wheels";
    inherit version;
    dontUnpack = true;
    nativeBuildInputs = with pkgs; [ python312Packages.pip python312Packages.setuptools python312 cacert ];
    buildPhase = ''
      export HOME=$TMPDIR
      export SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt
      pip download \
        --dest $TMPDIR/dl \
        --no-cache-dir \
        --no-deps \
        -r ${./requirements.txt}
      mkdir -p $out
      for f in $TMPDIR/dl/*; do
        case "$f" in
          *.whl) cp "$f" $out/ ;;
          *)     pip wheel --no-deps --no-cache-dir --wheel-dir $out "$f" ;;
        esac
      done
      find $out -exec touch -t 197001010000.00 {} +
    '';
    dontInstall = true;
    dontFixup = true;
    outputHashAlgo = "sha256";
    outputHashMode = "recursive";
    outputHash = "sha256-x4ThOzil8YgbvXBq5WslOIiV112IUVPkCpy18yTmSEI=";
  };
in
pkgs.stdenv.mkDerivation {
  pname = "indent";
  inherit version;
  dontUnpack = true;
  nativeBuildInputs = [ pkgs.uv pkgs.python312 ];
  buildPhase = ''
    export HOME=$TMPDIR
    uv venv $out/venv --python ${pkgs.python312}/bin/python3.12
    uv pip install \
      --python $out/venv/bin/python3.12 \
      --no-compile-bytecode \
      --no-index \
      --find-links ${wheels} \
      -r ${./requirements.txt}
    mkdir -p $out/bin
    ln -s $out/venv/bin/indent $out/bin/indent
  '';
  dontInstall = true;
}
