{
  description = "ClassCal iCalendar subscription service";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in {
      packages = forAllSystems (pkgs: rec {
        classcal = pkgs.callPackage ./nix/package.nix { };
        default = classcal;
      });

      checks = forAllSystems (pkgs: {
        classcal = self.packages.${pkgs.stdenv.system}.classcal;
      });

      nixosModules.default = {
        imports = [ ./nix/module.nix ];
        _module.args.self = self;
      };
    };
}
