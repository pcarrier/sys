{ self, ... }:
{
  perSystem =
    { pkgs, ... }:
    {
      devShells = {
        default = pkgs.mkShell {
          packages = with pkgs; [
            nixfmt
            nil
            nixd
            bun
            (writeShellScriptBin "hosts" ''
              echo ${builtins.toString (builtins.attrNames self.nixosConfigurations)}
            '')
          ];
        };
      };
    };
}
