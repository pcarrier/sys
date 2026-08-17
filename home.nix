{
  pkgs,
  nixpkgs-master,
  lib,
  system,
  systemType,
  baze,
  blit,
  tomorrowTheme,
  plenty,
  edl-ng,
  claude-desktop,
  codex-desktop,
  kimi-code,
  nix-vscode-extensions,
  trusted ? false,
  desktop ? false,
  ...
}:
{
  nixpkgs = {
    config = {
      allowUnfree = true;
      android_sdk.accept_license = true;
    };
    overlays = [
      nix-vscode-extensions.overlays.default
      # Let Chromium-based apps see PipeWire cameras.
      #
      # `WebRtcPipeWireCamera` is disabled by default in every Chromium
      # release so far, and without it Chromium enumerates only V4L2
      # `/dev/video*` devices — it never even contacts the camera portal, and
      # there is no fallback once its PipeWire factory is live. A camera that
      # exists only as a PipeWire node, which is the only kind Blit can lend,
      # is therefore invisible: a meeting reports no camera at all, and
      # because getUserMedia rejects wholesale when either half of an
      # audio+video request fails, it reports no microphone either.
      #
      # Every list below restates the features that package's own wrapper
      # already passes. Each of those wrappers puts its flags before `"$@"`,
      # and Chromium honours only the *last* `--enable-features` it is given,
      # so naming just the new feature silently drops the rest — hardware
      # decode and encode for Brave, screen sharing for the Electron apps.
      #
      # `WebRTCPipeWireCapturer`, which Slack and Legcord already set, is
      # *screen capture*. It is a different feature from the camera one and
      # does nothing for a webcam; the resemblance is a trap.
      (_: prev:
        let
          # Only Brave takes `commandLineArgs`; the Electron apps are wrapped
          # in place. In place, rather than joined into a new path, because a
          # generated desktop entry may hardcode an absolute path to this
          # derivation's own `bin/`, and wrapping that file keeps a launcher
          # and a shell invocation on the same binary.
          withCamera =
            package: binary: features:
            package.overrideAttrs (old: {
              nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ prev.makeWrapper ];
              postFixup = (old.postFixup or "") + ''
                wrapProgram $out/bin/${binary} \
                  --add-flags "--enable-features=${features},WebRtcPipeWireCamera"
              '';
            });
        in
        # Linux only: PipeWire is what this is about, and the Darwin builds of
        # these packages are `.app` bundles under `$out/Applications` with no
        # `$out/bin/<binary>` for `wrapProgram` to find.
        lib.optionalAttrs prev.stdenv.hostPlatform.isLinux {
          brave = prev.brave.override {
            commandLineArgs =
              "--enable-features=AcceleratedVideoDecodeLinuxGL,AcceleratedVideoEncoder,WaylandWindowDecorations,WebRtcPipeWireCamera";
          };
          slack = withCamera prev.slack "slack" "WaylandWindowDecorations,WebRTCPipeWireCapturer";
          legcord =
            withCamera prev.legcord "legcord"
              "UseOzonePlatform,WaylandWindowDecorations,WebRTCPipeWireCapturer";
        })
    ];
  };
  home-manager = {
    backupFileExtension = "backup";
    useGlobalPkgs = true;
    useUserPackages = true;
    users.pcarrier = lib.mkMerge (import ./home/config.nix {
      inherit
        pkgs
        nixpkgs-master
        lib
        system
        systemType
        baze
        blit
        tomorrowTheme
        plenty
        edl-ng
        claude-desktop
        codex-desktop
        kimi-code
        trusted
        desktop
        ;
    });
  };
}
