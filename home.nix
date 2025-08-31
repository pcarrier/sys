{
  pkgs,
  lib,
  systemType,
  trusted ? false,
  desktop ? false,
  ...
}:
let
  baseConfig = {
    home = {
      username = "pcarrier";
      homeDirectory = "/home/pcarrier";
      stateVersion = "25.11";
      packages = with pkgs; [
        bat
        claude-code
        codex
        fd
        fastfetch
        file
        htop
        jq
        moreutils
        mosh
        nixd
        nixfmt
        nmap
        nodejs
        ripgrep
        tree
        wget # for cursor
        yt-dlp
        zoxide
      ];
      sessionVariables = {
        EDITOR = "cursor --wait";
        VISUAL = "cursor --wait";
      };
    };
  };

  systemConfigs = {
    wsl = {
      home.packages = with pkgs; [ wslu ];
      home.sessionVariables.BROWSER = "wslview";
      home.sessionPath = [
        "/mnt/c/Users/pierr/AppData/Local/Programs/cursor/resources/app/bin"
      ];
      programs.fish.functions.pbcopy = {
        body = "clip.exe";
      };
    };
    bare = {
      home.packages = with pkgs; [
        brave
        code-cursor
        xclip
      ];
      home.sessionVariables.BROWSER = "brave";
      programs.fish.functions.pbcopy = {
        body = "xclip -selection clipboard";
      };
    };
  };

  desktopConfig = lib.mkIf desktop {
    home.packages = with pkgs; [
      _1password-gui
      vesktop
      slack
      spotify
      zoom-us
    ];
  };

  trustedConfig = lib.mkIf trusted {
    programs = {
      keychain = {
        enable = true;
        enableFishIntegration = true;
        keys = [ "id_ed25519" ];
      };
      git.signing = {
        format = "ssh";
        key = "~/.ssh/id_ed25519";
        signByDefault = true;
      };
    };
  };

  programsConfig = {
    programs = {
      direnv.enable = true;
      eza = {
        enable = true;
        enableFishIntegration = true;
      };
      fzf = {
        enable = true;
        enableFishIntegration = true;
      };
      aria2.enable = true;
      fd.enable = true;
      gh = {
        enable = true;
        settings.git_protocol = "ssh";
      };
      neovim = {
        enable = true;
        viAlias = true;
        vimAlias = true;
        extraLuaConfig = ''
          vim.opt.autoindent = true;
          vim.opt.smartindent = true;
          vim.opt.expandtab = true;
        '';
      };
      nh = {
        enable = true;
        clean.enable = true;
        flake = "/src/sys";
      };
      ssh = {
        enable = true;
        enableDefaultConfig = false;
        extraConfig = ''
          StrictHostKeyChecking accept-new
        '';
        matchBlocks = {
          "*" = {
            addKeysToAgent = "yes";
            compression = true;
          };
          ident = {
            hostname = "ident.me";
            port = 2222;
            user = "root";
          };
          tnedi = {
            hostname = "tnedi.me";
            port = 2222;
            user = "root";
          };
          srvus = {
            hostname = "srv.us";
            port = 2222;
          };
          x0 = {
            hostname = "x0.xmit.dev";
            port = 2222;
            user = "root";
          };
          x1 = {
            hostname = "x1.xmit.dev";
            port = 2222;
            user = "root";
          };
          x2 = {
            hostname = "x2.xmit.dev";
            port = 2222;
            user = "root";
          };
          horse = {
            hostname = "horse.pcarrier.com";
          };
          gorilla = {
            forwardAgent = true;
          };
        };
      };
      git = {
        enable = true;
        userName = "Pierre Carrier";
        userEmail = "pc@rrier.fr";
        lfs.enable = true;
        delta.enable = true;
        extraConfig = {
          pull.rebase = true;
          init.defaultBranch = "main";
          github.user = "pcarrier";
          color.ui = true;
          branch = {
            autoSetupRebase = "always";
            sort = "-committerdate";
          };
          column.ui = "auto";
          status.showUntrackedFiles = "normal";
          gc.auto = 0;
          log.date = "iso";
          fetch = {
            prune = true;
            pruneTag = true;
            all = true;
          };
          push = {
            default = "simple";
            autoSetupRemote = true;
            followTags = true;
          };
          rerere = {
            enabled = true;
            autoupdate = true;
          };
          rebase = {
            autosquash = true;
            autostash = true;
            updaterefs = true;
          };
          diff = {
            algorithm = "histogram";
            mnemonicPrefix = true;
            renames = true;
            noprefix = true;
          };
          grep.fullname = true;
          tag.sort = "version:refname";
          commit.verbose = true;
          help = {
            autocorrect = "prompt";
            format = "web";
          };
        };
        aliases = {
          aamend = "commit -av --amend --no-edit";
          amend = "commit -av --amend";
          b = "branch -v";
          bl = "blame -C -C -C";
          bu = "bundle";
          cl = "clone";
          cm = "commit -av";
          co = "checkout";
          cp = "cherry-pick";
          cu = "rebase master";
          d = "diff --patience";
          dc = "describe --contains";
          ds = "diff --staged --patience";
          dsc = "describe";
          dw = "diff --color-words --patience";
          ec = "config --global -e";
          fp = "push --force-with-lease";
          k = "log --graph --pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%ar) %C(bold blue)<%an>%Creset' --abbrev-commit --date=relative";
          kk = "log --no-merges --pretty=format:'%Cred%h%Creset -%C(yellow)%d%Creset %s %Cgreen(%ar) %C(bold blue)<%an>%Creset' --abbrev-commit --date=relative";
          nr = "name-rev --name-only --refs=refs/heads/*";
          nri = "name-rev --refs=refs/heads/* --stdin";
          pl = "pull";
          p = "push";
          pr = "!gh pr create -w";
          pulla = "pull --all";
          pusha = "push --all";
          ra = "rebase --abort";
          rc = "rebase --continue";
          ri = "rebase --interactive";
          sl = "shortlog -sn";
          ss = "status -sbuno";
          sss = "status -sb";
          st = "status";
          wd = "diff --word-diff --patience";
        };
      };
      lazygit.enable = true;
      fish = {
        enable = true;
        interactiveShellInit = ''
          set fish_greeting

          function __fish_postexec --on-event fish_postexec
            set_color yellow
            echo took $CMD_DURATION ms
            set_color normal
          end
        '';
        shellAliases = {
          C = "clear";
          c = "cursor";
          dl = "aria2c -x 16 -s 16 -j 16";
          g = "git";
          lg = "lazygit";
          n = "nh os switch /src/sys";
          t = "tmux attach";
          v = "nvim";
        };
        functions = {
          T = {
            body = "$argv 2>&1 | ts";
          };
          cm = {
            body = ''git cm -m "$argv"'';
          };
        };
      };
      tmux = {
        enable = true;
        historyLimit = 1000000;
        mouse = true;
        newSession = true;
        clock24 = true;
      };
      zoxide = {
        enable = true;
        enableFishIntegration = true;
      };
    };
  };
in
{
  home-manager = {
    backupFileExtension = "backup";
    useGlobalPkgs = true;
    useUserPackages = true;
    users = {
      pcarrier = lib.mkMerge [
        baseConfig
        (systemConfigs.${systemType} or { })
        desktopConfig
        trustedConfig
        programsConfig
      ];
    };
  };
}
