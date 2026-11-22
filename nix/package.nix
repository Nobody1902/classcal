{ lib
, stdenv
, makeWrapper
, python3
, source ? ../.
}:

let
  py = python3;
  runtimeDeps = with py.pkgs; [ fastapi httpx icalendar python-dotenv uvicorn ];
  checkDeps = [ py.pkgs.pytest ];
  pythonPath = py.pkgs.makePythonPath (runtimeDeps ++ checkDeps);

  cleanSrc = lib.cleanSourceWith {
    src = source;
    filter = path: type:
      let
        name = baseNameOf path;
      in
      ! (
        name == ".git"
        || name == ".devenv"
        || name == ".direnv"
        || name == ".env"
        || name == ".easistent_session"
        || name == ".pytest_cache"
        || name == ".ruff_cache"
        || name == "__pycache__"
        || name == "data"
        || name == "timetable.json"
        || lib.hasSuffix ".har" name
        || lib.hasSuffix ".pyc" name
      );
  };
in
stdenv.mkDerivation {
  pname = "classcal";
  version = "0.1.0";

  src = cleanSrc;

  nativeBuildInputs = [ makeWrapper ];

  dontBuild = true;

  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/classcal $out/bin
    cp -R . $out/share/classcal/
    makeWrapper ${py.interpreter} $out/bin/classcal \
      --set PYTHONPATH "$out/share/classcal:${pythonPath}" \
      --set PYTHONUNBUFFERED "1" \
      --add-flags "-m uvicorn main:app"

    runHook postInstall
  '';

  doCheck = true;
  checkPhase = ''
    runHook preCheck

    export PYTHONPATH="$PWD:${pythonPath}"
    ${py.interpreter} -m pytest -q

    runHook postCheck
  '';

  passthru.python = py;

  meta = with lib; {
    description = "iCalendar subscription endpoint for Easistent timetables";
    platforms = platforms.linux;
    mainProgram = "classcal";
  };
}
