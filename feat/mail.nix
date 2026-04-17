{
  services.dovecot2 = {
    enable = true;
    settings = {
      dovecot_config_version = "2.4.3";
      dovecot_storage_version = "2.4.3";
      protocols.imap = true;
      mail_driver = "maildir";
      mail_path = "~/Maildir";
    };
  };
}
