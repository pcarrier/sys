# Privileged compute remains under its existing account; the core stack never
# needs access to pcarrier's private /src trees or the rootful Docker socket.
{ lib, pkgs, ... }:
let
  state = "/var/lib/ultimator-sandboxes";
  core = "/var/lib/ultimator/ultimator";
  ready = "/var/lib/ultimator/.cluster-ready";
  projection = pkgs.writeShellApplication {
    name = "ultimator-project-sandbox";
    runtimeInputs = with pkgs; [
      coreutils
      gnugrep
      util-linux
      systemd
    ];
    text = builtins.readFile ./ultimator-sandbox-project.sh;
  };
in
{
  systemd.services.ultimator-sandbox-projection = {
    description = "Publish the sandbox role's credentials and worker binary";
    unitConfig.ConditionPathExists = ready;
    serviceConfig = {
      Type = "oneshot";
      ExecStart = lib.getExe projection;
      UMask = "0077";
    };
  };
  systemd.paths.ultimator-sandbox-projection = {
    wantedBy = [ "multi-user.target" ];
    pathConfig.PathChanged = [
      "${core}/.dev/credentials"
      "${core}/target/release/ultimatord"
      "${core}/target/release/ultimator"
      "${core}/.env.local"
      ready
    ];
  };
  systemd.services.ultimator-sandboxes = {
    description = "Ultimator sandbox compute worker (existing pcarrier boundary)";
    wantedBy = [ "multi-user.target" ];
    partOf = [ "ultimator.service" ];
    wants = [ "ultimator.service" ];
    requires = [ "ultimator-sandbox-projection.service" ];
    after = [
      "ultimator.service"
      "ultimator-sandbox-projection.service"
      "docker.service"
    ];
    unitConfig.ConditionPathExists = ready;
    path = with pkgs; [
      docker
      docker-buildx
      nix
      openssh
      bash
      coreutils
    ];
    environment = {
      HOME = "${state}/home";
      DOCKER_CONFIG = "${state}/home/.docker";
      ULTIMATOR_ROOT = state;
      ULTIMATOR_STATE = state;
      FLOWER_URL = "http://127.0.0.1:7301";
      ULTIMATOR_URL = "https://ultimator.app";
      ULTIMATOR_PUBLIC_URL = "https://ultimator.app";
      ULTIMATOR_SANDBOX_HOST = "7441800b-b5f9-40a8-92dd-d8c720006044";
      ULTIMATOR_SANDBOX_BINDS = "/src";
      ULTIMATOR_SANDBOX_RUNTIME = "yas";
      ULTIMATOR_SANDBOX_YAS_BIN = "/src/ultimator/.dev/yas/bin/yas";
      ULTIMATOR_YAS_BIN = "/src/ultimator/.dev/yas/current/yas";
      ULTIMATOR_RESTART_ON_REBUILD = "1";
      DOCKER_HOST = "unix:///var/run/docker.sock";
    };
    serviceConfig = {
      Type = "exec";
      User = "pcarrier";
      Group = "users";
      WorkingDirectory = state;
      EnvironmentFile = "${state}/runtime.env";
      ExecStart = "${state}/bin/ultimatord sandboxes";
      Restart = "always";
      RestartSec = 5;
      TimeoutStopSec = 30;
      KillMode = "mixed";
      UMask = "0077";
      LimitNOFILE = 1048576;
    };
  };
}
