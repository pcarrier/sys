# A GitHub Actions runner for the yas-run organisation, named after the host
# and labelled `nix` and the host's name, next to GitHub's own `self-hosted`,
# `Linux` and `X64` (or `ARM64`): `runs-on: [self-hosted, hound]`.
#
# It registers with /var/lib/secrets/github-runner.token, which holds either a
# registration token (org › Settings › Actions › Runners › New runner, or
# `gh api -X POST orgs/yas-run/actions/runners/registration-token --jq .token`;
# good for an hour) or a fine-grained PAT with read and write on the org's
# self-hosted runners. The runner keeps its own credentials in
# /var/lib/github-runner/<host> once registered, so a registration token is only
# needed again when the token file or the registration below changes; the
# service then registers anew, replacing the old runner of the same name.
# Write the file without a trailing newline (`printf %s`).
#
# yas-run/yas is public: the org's Default runner group allows public
# repositories, and the repository asks approval before running workflows from
# any outside contributor.
{
  config,
  pkgs,
  ...
}:
let
  host = config.networking.hostName;
  # Jobs work on disk rather than in the service's RuntimeDirectory: /run is a
  # tmpfs, and a Rust workspace's target directory outgrows it.
  work = "/var/lib/github-runner-work";
in
{
  users = {
    users.github-runner = {
      isSystemUser = true;
      group = "github-runner";
      home = work;
      description = "GitHub Actions runner";
    };
    groups.github-runner = { };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/secrets 0700 root root -"
    "d ${work} 0750 github-runner github-runner -"
    "d ${work}/${host} 0750 github-runner github-runner -"
  ];

  services.github-runners.${host} = {
    enable = true;
    url = "https://github.com/yas-run";
    tokenFile = "/var/lib/secrets/github-runner.token";
    name = host;
    replace = true;
    extraLabels = [
      "nix"
      host
    ];
    user = "github-runner";
    group = "github-runner";
    workDir = "${work}/${host}";
    # The module brings bash, coreutils, git, tar, gzip and Nix; this is what
    # workflow steps and actions reach for next.
    extraPackages = with pkgs; [
      curl
      file
      findutils
      gawk
      gnugrep
      gnused
      jq
      openssh
      unzip
      which
      xz
      zip
      zstd
    ];
    # This is a desktop too: jobs yield the CPU and disk to it and stop short
    # of its memory. (Builds `nix build` hands to the daemon run in the
    # daemon's cgroup, not this one.)
    serviceOverrides = {
      CPUWeight = 20;
      IOWeight = 20;
      MemoryHigh = "40G";
      MemoryMax = "48G";
    };
  };
}
