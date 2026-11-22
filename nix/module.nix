{ self, lib, pkgs, config, ... }:

let
  cfg = config.services.classcal;

  reservedSettings = [ "DATA_DIR" "TIMETABLE_DIR" "CALENDAR_DIR" "USERNAME" "PASSWORD" ];

  envValue = value:
    if lib.isBool value then
      lib.boolToString value
    else if lib.isString value then
      lib.escapeShellArg value
    else
      toString value;

  baseEnvironment = [
    "CLASSCAL_DATA_DIR=${cfg.stateDir}"
    "CLASSCAL_TIMETABLE_DIR=${cfg.stateDir}/timetable"
    "CLASSCAL_CALENDAR_DIR=${cfg.stateDir}/calendars"
  ];

  settingsEnvironment = lib.mapAttrsToList (
    name: value: "CLASSCAL_${name}=${envValue value}"
  ) cfg.settings;
in
{
  options.services.classcal = {
    enable = lib.mkEnableOption "ClassCal iCalendar subscription service";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.system}.classcal;
      defaultText = lib.literalExpression "self.packages.<system>.classcal";
      description = "The ClassCal package to run.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "classcal";
      description = "System user that runs the service.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "classcal";
      description = "System group that runs the service.";
    };

    stateDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/classcal";
      description = "Directory for weekly timetable JSON files and generated calendars.";
    };

    listenAddress = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address passed to uvicorn --host.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = "Port passed to uvicorn --port.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = ''
        File containing CLASSCAL_USERNAME and CLASSCAL_PASSWORD.
        Keep it outside the Nix store with mode 0600; systemd reads it before dropping privileges.
      '';
    };

    settings = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.oneOf [ lib.types.str lib.types.int lib.types.bool ]
      );
      default = { };
      example = {
        TIMEZONE = "Europe/Ljubljana";
        SCHOOL_YEAR_START = "2026-09-01";
        CACHE_TTL_CURRENT = 3600;
      };
      description = ''
        Additional config.py settings, without the CLASSCAL_ prefix.
        Each key is exported as CLASSCAL_<key>.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.all (
          name: builtins.match "[A-Za-z_][A-Za-z0-9_]*" name != null
        ) (builtins.attrNames cfg.settings);
        message = "services.classcal.settings keys must be valid environment variable names.";
      }
      {
        assertion = lib.all (name: !(lib.elem name reservedSettings))
          (builtins.attrNames cfg.settings);
        message = ''
          services.classcal.settings must not contain ${lib.concatStringsSep ", " reservedSettings};
          use stateDir and environmentFile instead.
        '';
      }
    ];

    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.group;
      home = cfg.stateDir;
      createHome = true;
      description = "ClassCal service user";
    };

    users.groups.${cfg.group} = { };

    systemd.services.classcal = {
      description = "ClassCal iCalendar subscription service";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = cfg.stateDir;
        Environment = baseEnvironment ++ settingsEnvironment;
        EnvironmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;
        ExecStart = "${lib.getExe cfg.package} --host ${cfg.listenAddress} --port ${toString cfg.port} --workers 1";
        Restart = "on-failure";
        RestartSec = "5s";

        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ cfg.stateDir ];
      };
    };
  };
}
