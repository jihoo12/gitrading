{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    (pkgs.python3.withPackages (ps: with ps; [
      requests
      cairosvg
      pillow
    ]))
  ];

  shellHook = ''
    echo "Python 개발 환경이 활성화되었습니다!"
    python --version
  '';
}
