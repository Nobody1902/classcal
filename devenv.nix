{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

{
  packages = [ pkgs.ruff ];

  languages.python = {
    enable = true;
    version = "3.11";
    venv = {
      enable = true;
      requirements = ./requirements.txt;
    };
  };
}
