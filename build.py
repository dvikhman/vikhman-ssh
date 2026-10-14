import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main():
    import PyInstaller.__main__

    PyInstaller.__main__.run([
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name=VikhmanSSH",
        f"--distpath={ROOT / 'dist'}",
        f"--workpath={ROOT / 'build' / 'pyinstaller'}",
        "--collect-all=PySide6.QtPdf",
        "--collect-all=PySide6.QtPdfWidgets",
        "--collect-all=PySide6.QtMultimedia",
        "--collect-all=PySide6.QtMultimediaWidgets",
        "--collect-data=pygments",
        "--collect-all=keyring",
        "--copy-metadata=keyring",
        "--add-data=" + os.pathsep.join((str(ROOT / "locales"), "locales")),
        str(ROOT / "main.py"),
    ])


if __name__ == "__main__":
    main()
