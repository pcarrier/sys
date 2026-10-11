# Ultimator's releases from artifacts on hound (xmit-dev/ultimator's "deploy in a minute", report
# https://ultimator.app/reports/4hv26gd6exthtllp; the repository's deploy/hound/README.md and deploy/cd.sh): hound is the
# warm builder of every release's artifacts and serves them to the cluster (i0-i2) over the WireGuard mesh.
#
#   - the build user, ultimator-release: bin/release-build runs as it (deploy/cd.sh, from the release unit of
#     feat/ultimator-cluster-cd.nix, through sudo), with Docker (the builder image) and its warm state in
#     /var/lib/ultimator-release-build (checkouts, the cargo target, caches: 15-25 GB, kept between runs). It reads the
#     release unit's checkout and its Flower and YAS siblings (group users), never pcarrier's deploy key (0600). It
#     alone reads the release-signing key, /etc/ultimator-release/release-key.pem;
#   - the artifact store, /var/lib/ultimator-artifacts (its, world-readable), served read-only on 10.77.0.20:7790
#     (wg-ultimator only) by `ultimatord artifacts serve`: the hosts' release agents and operators read it
#     (ULTIMATOR_ARTIFACTS_URL). What it serves is signed and content-addressed: the transport needn't be trusted;
#   - ultimator-cluster-assets: the client assets (the CLI's downloads, the YAS frame, the desktop app) of the last
#     release deploy/cd.sh put live (/var/lib/ultimator-cluster-release/assets-wanted), built after the release and
#     beside the next ones, never before them (deploy/nixos/release.sh --assets-only in a checkout of its own). Started
#     by deploy/cd.sh; one at a time, and a run that ends finds the newest commit wanted and builds that.
#
# Nothing changes how main goes out until /var/lib/ultimator-cluster-release/mode says `releases` (deploy/cd.sh; the
# cut-over runbook). Once, by hand, before that (deploy/hound/README.md "Once, by hand"):
#   sudo install -d -m 0755 /etc/ultimator-release
#   sudo -u ultimator-release openssl genpkey -algorithm ed25519 -out /etc/ultimator-release/release-key.pem
#   sudo chmod 0400 /etc/ultimator-release/release-key.pem
#   echo '<the operator hash>' | sudo tee /etc/ultimator-release/operator-hash      # computed on i0
#   sudo install -D -m 0755 <a release's bin/ultimatord> /var/lib/ultimator-artifacts-server/ultimatord
#   sudo systemctl start ultimator-artifacts
{
  config,
  lib,
  pkgs,
  ...
}:
let
  user = "ultimator-release";
  build = "/var/lib/ultimator-release-build";
  store = "/var/lib/ultimator-artifacts";
  server = "/var/lib/ultimator-artifacts-server/ultimatord";
  release = "/var/lib/ultimator-cluster-release";
  logs = "/var/log/ultimator-cluster-release";
in
lib.mkIf (config.networking.hostName == "hound") {
  users = {
    users.${user} = {
      isSystemUser = true;
      group = user;
      home = build;
      # docker: the builder image; users: the release unit's checkout (/var/lib/ultimator-cluster-release, 0750
      # pcarrier:users), which bin/release-build fetches the commit from.
      extraGroups = [
        "docker"
        "users"
      ];
      description = "Ultimator's release builder (bin/release-build)";
    };
    groups.${user} = { };
  };

  systemd.tmpfiles.rules = [
    "d ${build} 0750 ${user} ${user} -"
    "d ${store} 0755 ${user} ${user} -"
    "d ${store}/layers 0755 ${user} ${user} -"
    "d ${store}/releases 0755 ${user} ${user} -"
    "d /etc/ultimator-release 0755 root root -"
    "d /var/lib/ultimator-artifacts-server 0755 root root -"
  ];

  # deploy/cd.sh unpacks the release's ultimatord from its server layer; bin/release-build archives them. (The layers'
  # binaries ask for the standard loader, which programs.nix-ld (base/common.nix) provides.)
  environment.systemPackages = [ pkgs.zstd ];

  systemd.services.ultimator-artifacts = {
    description = "Ultimator's release artifacts, read-only, for i0-i2 (http://10.77.0.20:7790)";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "wireguard-wg-ultimator.service"
    ];
    wants = [ "network-online.target" ];
    # Seeded once by hand from a release's server layer (above): until then, nothing to start.
    unitConfig.ConditionPathExists = server;
    serviceConfig = {
      User = user;
      Group = user;
      ExecStart = "${server} artifacts serve --dir ${store} --listen 10.77.0.20:7790";
      Restart = "always";
      RestartSec = 5;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ReadOnlyPaths = [ store ];
      NoNewPrivileges = true;
    };
  };
  networking.firewall.interfaces.wg-ultimator.allowedTCPPorts = [ 7790 ];

  systemd.services.ultimator-cluster-assets = {
    description = "Build and install the client assets of Ultimator's last release";
    # As the release unit (feat/ultimator-cluster-cd.nix): pcarrier's git, Nix and sudo, the system's tools.
    path = [
      "/run/wrappers"
      "/etc/profiles/per-user/pcarrier"
      "/run/current-system/sw"
    ];
    restartIfChanged = false;
    stopIfChanged = false;
    serviceConfig = {
      Type = "oneshot";
      User = "pcarrier";
      Group = "users";
      WorkingDirectory = release;
      StandardOutput = "append:${logs}/assets.log";
      StandardError = "inherit";
      # The downloads (~7 min), the YAS frame (~1 min), the desktop app (up to 90 min when desktop/ changed).
      TimeoutStartSec = "200min";
    };
    script = ''
      set -uo pipefail
      base=${release}/assets
      checkout=$base/ultimator
      mkdir -p "$base"
      while :; do
        if [ -e ${release}/hold ]; then
          echo "$(date -u +%FT%TZ) held (${release}/hold): not building the client assets"
          exit 0
        fi
        wanted=$(tr -d '[:space:]' <${release}/assets-wanted 2>/dev/null)
        built=$(tr -d '[:space:]' <"$base/built" 2>/dev/null)
        [ -n "$wanted" ] && [ "$wanted" != "$built" ] || exit 0
        echo "$(date -u +%FT%TZ) client assets of ''${wanted:0:12}"
        if [ ! -d "$checkout/.git" ]; then
          rm -rf "$checkout"
          git clone --quiet --no-checkout git@github.com:xmit-dev/ultimator.git "$checkout" || exit 1
        fi
        cd "$checkout" || exit 1
        git cat-file -e "$wanted^{commit}" 2>/dev/null || git fetch --quiet origin main || exit 1
        git checkout --quiet --force --detach "$wanted" && git clean --quiet -ffdx || exit 1
        export CLUSTER_KNOWN_HOSTS=/etc/ultimator-cluster/known-hosts
        export CLUSTER_SSH_KEY=${release}/deploy-key
        [ -r "$CLUSTER_SSH_KEY" ] || CLUSTER_SSH_KEY=$HOME/.ssh/id_ed25519
        export DEPLOY_REPORT_RUN="assets-''${wanted:0:12}"
        if deploy/nixos/release.sh --assets-only; then
          printf '%s\n' "$wanted" >"$base/built"
        else
          # The next release's run starts this again; its keys still differ, so it builds them again.
          echo "$(date -u +%FT%TZ) the client assets of ''${wanted:0:12} failed"
          exit 1
        fi
      done
    '';
  };
}
