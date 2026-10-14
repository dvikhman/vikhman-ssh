# VikhmanSSH — Detailed Functional Description and User Guide

## 1. Overview

VikhmanSSH is a desktop application for managing remote servers over SSH. It is designed for system administrators, developers, and DevOps engineers who need a fast, reliable way to connect to Linux or Unix-like machines, work in the remote terminal, browse remote files, upload or download files, edit remote text documents, and inspect media content without leaving the application.

The project is implemented as a Qt-based desktop tool using PySide6 and Paramiko. It supports multiple concurrent SSH sessions, multiple language interfaces, secure host key validation, and a built-in remote file browser with previews and editing.

The key idea of the program is to keep the user in a single environment where they can:

- connect to multiple remote hosts,
- switch between active terminals,
- execute shell commands interactively,
- inspect and modify remote files,
- manage directories and file transfers,
- review logs and errors,
- use a localized interface in several languages.

---

## 2. Main Functional Capabilities

### 2.1 Multi-server connection management

The application can keep several remote connections open at the same time. Each server is represented as a separate item in the server list. The list may show connection state and status indicators, so the user can quickly understand which hosts are online, offline, disconnected, or currently active.

The main workflow is:

1. add a server profile,
2. enter connection parameters,
3. choose authentication mode,
4. open a session,
5. work in the terminal or file manager.

The user may create multiple profiles for different environments such as staging, testing, production, internal infrastructure, or personal development machines.

### 2.2 Remote terminal

Each server has its own terminal session. The terminal is built on a pseudo-terminal (PTY) and supports:

- direct keyboard passthrough,
- ANSI color output,
- terminal resizing,
- mouse events,
- text selection and copy/paste support,
- multiple active sessions in parallel.

This makes the shell experience close to a real SSH session in a terminal emulator. The app also shows SSH authentication stages and connection errors in a log panel above the terminal. This helps diagnose problems with remote authentication, SSH config, host keys, or sudo access.

### 2.3 Automatic host key trust and verification

On first connection to an unknown host, the application accepts the SSH host key automatically and stores it in the local application data directory. This protects the user from interruptions and allows first-time access without additional manual configuration.

On subsequent connection attempts, the application validates the saved host key against the actual server key. If there is a mismatch, the connection is rejected. This is a typical TOFU model (Trust On First Use): the first key is trusted, and later mismatches are treated as suspicion.

The key fingerprint is displayed in the connection log, allowing the user to verify it through a trusted out-of-band channel for high-value systems.

### 2.4 Server profile storage and authentication modes

The application stores server profiles in a configuration file inside the platform-specific application data directory. This includes the host, port, username, and optionally the SSH key path or authentication method. The app does not save passwords or passphrases in the profile file.

Supported authentication patterns include:

- password-based SSH authentication,
- SSH agent-based authentication,
- private key authentication using a key file path,
- interactive authentication when the environment needs it.

Passwords are stored in the system keychain instead of plain text files when the OS supports it. This reduces the risk of accidental leakage through configuration files.

### 2.5 File manager for remote files

The file manager operates through SFTP and allows users to:

- browse remote directories,
- navigate folders,
- upload local files to the remote server,
- download remote files to the local machine,
- create folders,
- rename or delete items,
- view metadata and directories.

This is especially useful for copying configuration files, logs, scripts, deployment artifacts, and project directories.

### 2.6 File preview and viewing

The application includes a built-in viewer for several content types. Supported categories include:

- images,
- PDFs,
- audio files,
- video files.

Other file types are opened in the system application or default external handler.

Preview mode downloads a temporary copy of the remote file, up to a configurable maximum size. In the current implementation, the cap is 512 MB. This allows previewing large documents or media files without moving the full file into the user’s local workspace permanently.

### 2.7 Remote text editor with syntax highlighting

The application supports editing text files directly on the remote host. This is useful when a user needs to modify config files, shell scripts, YAML, JSON, Python, JavaScript, and similar files.

Key functions:

- double-click a remote file to open it,
- syntax highlighting based on the file extension,
- save changes via Ctrl+S,
- UTF-8 text file support up to 2 MB,
- automatic conflict check before saving.

Before saving a file, the application checks whether the remote file has changed since it was opened. This reduces the chance of overwriting another user’s edits or a process that modified the file while the editor window was open.

### 2.8 Root / sudo mode for file operations

The file manager contains a Root / sudo option. When enabled, it starts the SFTP server through sudo as needed. This allows file operations to run with elevated privileges on the remote machine.

Behavior:

- if sudo does not require a password, the mode starts immediately,
- if a password is needed, the app prompts for the sudo password,
- the password is not saved locally,
- the remote system must have a working sudo configuration and the `sftp-server` executable available.

This feature is useful for administrators who need to read, edit, or move files in protected system directories.

### 2.9 Localization and multi-language support

The program supports multiple interface languages. The default language can be changed in Settings. Available languages include:

- Arabic,
- Chinese,
- English,
- French,
- Kazakh,
- Russian,
- Spanish.

The interface language is stored in the application settings. When a language selection is changed, the app restarts so the new locale is applied consistently across all modules.

The translation files live in the project’s `locales/` directory. Each file is JSON-based and contains a mapping of original Russian interface strings to the target language.

### 2.10 Data and configuration storage

The application stores user data in the platform-specific config directory:

- Windows: `%APPDATA%\ServerManager`
- macOS: `~/Library/Application Support/ServerManager`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/server-manager`

Files typically stored there include:

- `servers.json` — server profiles,
- `settings.json` — interface and app settings,
- `known_hosts` — trusted SSH host keys,
- keyring credentials for password storage when supported by the OS.

This layout keeps the program portable but still consistent with the host operating system’s expectations.

---

## 3. Getting Started

### 3.1 Requirements

To run the project from source, you need:

- Python 3.9 or newer,
- a graphical desktop session on Linux,
- access to installed dependencies from `requirements.txt`,
- SSH access to the servers you want to connect to.

### 3.2 Manual installation

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

### 3.3 Launch scripts

The repository includes launch and build scripts:

- Linux/macOS: `./run.sh`
- Windows: `run.bat`

The scripts automatically create a virtual environment when needed and install required dependencies. This makes first-time setup easier and ensures the application runs in a controlled Python environment.

### 3.4 Standalone build

To build a distributable standalone package:

- Linux/macOS: `./build.sh`
- Windows: `build.bat`

The resulting bundle is created in `dist/VikhmanSSH` on most systems (with a `.app` bundle on macOS). This is useful when you need to distribute the app to users without requiring Python or project dependencies on the target machine.

> Important: build each target platform on that platform. PyInstaller does not support cross-compiling between operating systems.

---

## 4. Typical User Workflow

### 4.1 Creating a server profile

In the main application, add a new server profile with:

- connection name,
- hostname or IP address,
- SSH port,
- username,
- SSH key path or password method,
- optional extra settings.

The profile is then saved locally for future use.

### 4.2 Opening a connection

Once a server profile is created, the user can connect. During connection, the application performs the SSH handshake, verifies the host key, and shows the authentication stage in the log.

If the server key is new, it is accepted and saved to the local host key store. If the key is already known but changed, the app rejects the connection.

### 4.3 Working in the terminal

After connecting, the user sees the remote shell. Commands are sent directly to the server, and output is displayed in the terminal UI. Features such as ANSI colors, PTY resizing, and mouse events make the experience close to a native terminal emulator.

The user may keep several remote terminal windows open simultaneously and switch between servers from the server list.

### 4.4 Working with remote files

By switching to the Files tab for a connected server, the user can:

- open remote directories,
- upload local files,
- download files,
- create directories,
- remove or rename files,
- inspect files before download,
- open text documents in the built-in editor,
- preview images, PDFs, audio, and video files.

### 4.5 Using root/sudo mode

When the file system requires administrative access, enable Root / sudo. This can be useful for editing system configuration files or moving protected files. This should be used only when the user has explicit authorization and a legitimate administrative need.

---

## 5. Security Notes

Although the tool is designed for convenience and productivity, it touches privileged infrastructure. Users should keep these principles in mind:

- confirm host fingerprints before trusting a server,
- avoid saving sensitive data in plain-text configuration files,
- protect local application data directories,
- use SSH keys rather than insecure passwords where possible,
- review sudo requirements carefully before enabling administrative SFTP mode,
- prefer trusted, audited infrastructure for production systems.

The application follows a minimal security philosophy: it does not store passwords in the server profile file, uses OS keyring capabilities when available, and verifies host keys on subsequent connections.

---

## 6. Troubleshooting

### Problem: The application does not start

Possible causes:

- Python dependencies are not installed,
- no graphical session is available on Linux,
- the virtual environment is missing or broken,
- the local OS blocks the application due to missing graphical libraries.

Solution:

- install dependencies with `python -m pip install -r requirements.txt`,
- run inside a graphical session,
- verify Python version compatibility,
- use the project launch scripts to create the correct environment automatically.

### Problem: Connection is rejected

Possible causes:

- wrong username or password,
- invalid SSH key path,
- missing SSH agent configuration,
- host key mismatch,
- SSH service not running on the remote host.

Solution:

- verify credentials,
- check host key fingerprints,
- ensure the server allows the configured SSH method,
- inspect the log panel for SSH handshake errors.

### Problem: File commands fail with permission errors

Possible causes:

- remote account lacks privilege for the target folder,
- sudo is not configured,
- the remote host does not have the `sftp-server` binary installed.

Solution:

- enable Root / sudo mode when permitted,
- verify sudo access,
- ensure the remote system exposes `sftp-server` in the expected location.

### Problem: Large files do not preview correctly

Possible causes:

- file size exceeds preview limits,
- the file type is not supported by the built-in viewer,
- remote transfer or permission restrictions are blocking preview.

Solution:

- use the local file system or a dedicated viewer if needed,
- keep file previews below the configured limit,
- download the file if the built-in preview format is unsupported.

---

## 7. Supported Platforms and Environment Notes

The tool is designed for desktop use on Windows, macOS, and Linux. The project includes shell scripts to simplify local setup and building. For production deployment, the recommended approach is to build the app on the target OS and distribute the resulting bundle there.

Because it relies on Qt and a graphical interface, it is best run in a user session with display access. On Linux, a GUI session is required for the application window to open.

---

## 8. Summary

VikhmanSSH combines the essential features needed for practical remote workstation management:

- interactive SSH terminal,
- multiple active sessions,
- file transfer and management,
- remote file editing,
- media preview,
- host key verification,
- localization,
- secure authentication integration.

This makes it useful for everyday administration, remote debugging, configuration changes, and operational workflows where quick access to a server is needed without opening a separate terminal emulator or file transfer tool.
