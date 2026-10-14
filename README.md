In accordance with the legislation of the Republic of Kazakhstan, the information in this file is provided in English.

# VikhmanSSH

Author: Dmitriy Vikhman

A desktop application for managing servers over SSH and working in a remote shell.

## Screenshots

- [Main application window](screenshots/1.png) — Main interface with the server list, connection log, and terminal panel.
- [Server connection form](screenshots/2.png) — Creating or editing an SSH profile and connection settings.
- [Remote terminal session](screenshots/2026-10-14_18-16-54.png) — Active terminal session connected to a remote server with command output.
- [Remote file browser](screenshots/2026-10-14_18-20-05.png) — Browsing remote directories and managing files on the server.
- [File transfer actions](screenshots/2026-10-14_18-26-45.png) — Uploading and downloading files between the local machine and the remote host.
- [Built-in file editor](screenshots/2026-10-14_18-28-47.png) — Editing a text file directly in the app with syntax highlighting.
- [Sudo / root file access](screenshots/2026-10-14_18-30-44.png) — Accessing remote files with elevated privileges through the SFTP root mode.
- [Application settings](screenshots/3.png) — Language and application settings screen with interface customization options.

## Launch

Run the source project on Linux/macOS:

```bash
./run.sh
```

On Windows, run `run.bat`. The launch scripts create a virtual environment when needed and install dependencies.

## Standalone build

Build the application with Python and its libraries bundled on the target operating system:

```bash
./build.sh
```

On Windows, use `build.bat`. The output is created in `dist/VikhmanSSH` (macOS also produces a `VikhmanSSH.app` bundle). You can copy it to another machine running the same operating system without installing Python or the project dependencies. Build each platform on that platform: PyInstaller does not cross-compile between operating systems.

## Interface language

Open **Settings** in the main window and choose Kazakh or one of the six official UN languages: Arabic, Chinese, English, French, Russian, or Spanish. The application restarts to apply the selected language to all windows and modules. The language selection and application data are stored in the user's platform-specific application directory: `%APPDATA%\ServerManager` on Windows, `~/Library/Application Support/ServerManager` on macOS, and `${XDG_CONFIG_HOME:-~/.config}/server-manager` on Linux.

To add a language, create a JSON file in `locales/` using `en.json` as a template:

```json
{
  "code": "de",
  "name": "Deutsch",
  "translations": {
    "Настройки": "Einstellungen",
    "Подключиться": "Verbinden"
  }
}
```

Dictionary keys are the original Russian interface strings. Missing translations fall back to Russian; the new language appears in Settings after restarting the application.

Manual installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

On Linux, a graphical session is required to launch the GUI. On the first connection, an unknown SSH host key is accepted automatically and saved to `known_hosts` in the application data directory. Later connections verify the saved key and reject mismatches. The key fingerprint is shown in the connection log. This is a trust-on-first-use (TOFU) model; verify fingerprints for sensitive servers through an independent trusted channel.

Server profiles are stored in `servers.json` in the application data directory. Passwords and passphrases are not saved. You can authenticate with an SSH agent or specify a key file path in the profile.

The built-in terminal sends keyboard input directly and supports ANSI colors, PTY resizing, and standard mouse events. Use `Ctrl+Shift+V` to paste.

You can keep multiple SSH sessions open at once. Select a server in the list to switch to its terminal; the colored indicator next to the name shows the connection state. Disconnecting clears only that server's terminal screen. SSH authentication stages and connection errors appear in the log above the terminal. The log can be shown or hidden on each server page.

Each server page has **Terminal** and **Files** tabs. The file manager can browse remote directories, upload and download files, create directories, and delete items. Images, PDFs, audio, and video open in the built-in viewer; other documents open in the system application. Preview downloads a temporary copy of up to 512 MB. Text files can also be edited: double-clicking a file opens the syntax-highlighted editor for its extension, and `Ctrl+S` saves changes. UTF-8 files up to 2 MB are supported. Before saving, the application checks whether the remote file has changed since it was opened.

The file manager includes a `Root / sudo` option. It starts the SFTP server through `sudo`; the mode is enabled immediately when no password is required, otherwise the application prompts for the sudo password. The password is not stored. This feature requires sudo permission for the connected user and the system `sftp-server` executable on the remote host.
