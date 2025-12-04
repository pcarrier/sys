{ pkgs, ... }:
{
  systemd.services.libk = {
    description = "Register 16al.libk.org with libk.org";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.curl}/bin/curl -X POST -4sfu 16al:$(cat /etc/libkp) https://libk.org";
    };
  };

  systemd.timers.libk = {
    description = "Run libk registration every minute";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "minutely";
      Persistent = true;
    };
  };
}
