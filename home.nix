{
  pkgs,
  nixpkgs-master,
  lib,
  nix-index,
  system,
  systemType,
  baze,
  tomorrowTheme,
  trusted ? false,
  desktop ? false,
  ...
}:
let
  pkgs-master = import nixpkgs-master { inherit system; };
  baseConfig = {
    home = {
      stateVersion = "25.11";
      username = "pcarrier";
      homeDirectory = "/home/pcarrier";
      packages = with pkgs; [
        bat
        baze.packages.${system}.default
        bubblewrap
        pkgs-master.codex
        dive
        fd
        fastfetch
        ffmpeg
        file
        gnuplot
        htop
        jq
        ldns
        lsof
        moreutils
        mosh
        mpv
        ncdu
        nixd
        nix-index.packages.${system}.default
        nixfmt
        nmap
        nodejs
        pssh
        ripgrep
        tokei
        tree
        yt-dlp
        wget # for remote vscode
        zoxide
      ];
      sessionVariables = {
        EDITOR = "code --wait";
        VISUAL = "code --wait";
      };
    };
  };

  systemConfigs = {
    wsl = {
      home = {
        packages = with pkgs; [ wslu ];
        sessionVariables.BROWSER = "wslview";
        sessionPath = [
          "/mnt/c/Users/pierr/AppData/Local/Programs/Microsoft VS Code/bin"
        ];
      };
      programs.fish.functions.pbcopy = {
        body = "clip.exe";
      };
    };
    bare = {
      home = {
        packages = with pkgs; [
          brave
          kdePackages.krdc
          simplescreenrecorder
          xclip
        ];
        sessionVariables.BROWSER = "brave";
      };
      programs = {
        vscode = {
          enable = true;
          profiles.default.extensions = with pkgs.vscode-extensions; [
            denoland.vscode-deno
            eamodio.gitlens
            editorconfig.editorconfig
            esbenp.prettier-vscode
            github.copilot
            github.vscode-github-actions
            github.vscode-pull-request-github
            golang.go
            hashicorp.terraform
            jnoortheen.nix-ide
            mkhl.direnv
            ms-azuretools.vscode-containers
            ms-azuretools.vscode-docker
            #ms-edgedevtools.vscode-edge-devtools
            ms-kubernetes-tools.vscode-kubernetes-tools
            ms-vscode-remote.remote-containers
            ms-vscode-remote.remote-ssh
            ms-vscode-remote.remote-ssh-edit
            #ms-vscode-remote.remote-wsl
            ms-vscode.live-server
            ms-vscode.remote-explorer
            ms-windows-ai-studio.windows-ai-studio
            #nimsaem.nimvscode
            oderwat.indent-rainbow
            #openai.chatgpt
            redhat.vscode-yaml
            rust-lang.rust-analyzer
            tamasfe.even-better-toml
            usernamehw.errorlens
          ];
        };
        fish.functions.pbcopy = {
          body = "xclip -selection clipboard";
        };
      };
    };
  };

  desktopConfig = lib.mkIf desktop {
    home.packages = with pkgs; [
      _1password-gui
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
      neovim =
        let
          tomorrow = pkgs.vimUtils.buildVimPlugin {
            pname = "tomorrow-theme";
            version = "master";
            src = "${tomorrowTheme}/vim";
          };
        in
        {
          enable = true;
          viAlias = true;
          vimAlias = true;
          extraLuaConfig = ''
            vim.opt.autoindent = true
            vim.opt.smartindent = true
            vim.opt.expandtab = true
            vim.opt.number = true
            vim.opt.relativenumber = true
            vim.opt.cursorline = true
            vim.opt.colorcolumn = "100"
            vim.opt.termguicolors = true

            require('supermaven-nvim').setup({})
            local lsp = require('lspconfig')
            lsp.nixd.setup({})
            lsp.rust_analyzer.setup({})

            vim.api.nvim_create_autocmd("BufWritePre", {
              callback = function(args)
                vim.lsp.buf.format({
                  bufnr = args.buf,
                  async = false,
               })
              end,
            })
            vim.cmd.colorscheme('Tomorrow-Night-Bright')
          '';
          plugins = with pkgs.vimPlugins; [
            tomorrow
            nvim-lspconfig
            vim-nix
            supermaven-nvim
            nvim-treesitter.withAllGrammars
          ];
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
            controlMaster = "auto";
            controlPath = "~/.ssh/control-%r@%h:%p";
            controlPersist = "1h";
            serverAliveInterval = 60;
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
          komodo = {
            forwardAgent = true;
          };
          monster = {
            forwardAgent = true;
          };
        };
      };
      git = {
        enable = true;
        package = pkgs.gitFull;
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
      lazygit = {
        enable = true;
        settings = {
          git.overrideGpg = true;
        };
      };
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
          a = "codex --model gpt-5-codex --full-auto --search";
          C = "clear";
          c = "code";
          ca = "cargo";
          dl = "aria2c -x 16 -s 16 -j 16";
          g = "git";
          lg = "lazygit";
          n = "nh os switch";
          t = "tmux attach";
          v = "nvim";
          km = "kubectl --context minikube";
          ks = "kubectl --namespace sandbox --context gke_twin-multiverse-sandbox_europe-west9_twin-multiverse-sandbox-paris";
          kpp = "kubectl --namespace prod --context gke_twin-multiverse-prod_europe-west9_twin-multiverse-prod-paris";
          kpi = "kubectl --namespace prod --context gke_twin-multiverse-prod_us-central1_twin-multiverse-prod-iowa";
          kps = "kubectl --namespace sandbox --context gke_twin-multiverse-prod_asia-southeast1_twin-multiverse-prod-singapore";
        };
        functions = {
          T = {
            body = "$argv 2>&1 | ts";
          };
          cm = {
            body = ''g cm -m "$argv"'';
          };
          nu = {
            body = ''
              set -l ref (git -C /src/sys rev-parse HEAD)
              for host in $argv
                echo === $host ===
                ssh $host nh os switch github:pcarrier/sys/$ref
              end
            '';
          };
          nuke = {
            body = ''
              set -l ref (git -C /src/sys rev-parse HEAD)
              for host in $argv
                echo === $host ===
                ssh root@$host nixos-rebuild switch github:pcarrier/sys#$ref
              end
            '';
          };
          rc = {
            body = ''
              cursor --folder-uri=vscode-remote://ssh-remote+$1(pwd)
            '';
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
