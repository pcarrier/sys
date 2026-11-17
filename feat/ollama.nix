{ lib, ... }:
{
  services.ollama = {
    enable = true;
    # DynamicUser keeps StateDirectory below /var/lib/private, which normal
    # users cannot traverse. A stable account lets the shared `users` group
    # retain read access to Ollama's state and models.
    user = "ollama";
    group = "users";
  };

  systemd.services.ollama.serviceConfig = {
    StateDirectoryMode = "0750";
    UMask = lib.mkForce "0027";
  };

  systemd.tmpfiles.rules = [
    # Existing DynamicUser state remains behind this parent after switching to
    # a static user. /var is ZFS with ACLs disabled, so use the directory's
    # group execute bit to let normal users traverse, but not list, the parent.
    "z /var/lib/private 0710 root users -"
    # Migrate existing state to group-readable modes without adding execute to
    # regular files; future files inherit equivalent modes from UMask above.
    "Z /var/lib/private/ollama ~0750 ollama users -"
  ];
}
