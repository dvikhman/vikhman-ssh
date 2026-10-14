import base64
import codecs
import hashlib
import json
import mimetypes
import os
import posixpath
import queue
import shlex
import shutil
import stat
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

import paramiko
import pyte
import keyring
from PySide6.QtCore import QTime, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QShortcut,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QSizePolicy,
    QStackedWidget,
    QSpinBox,
    QSlider,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from wcwidth import wcswidth
from pygments import lex
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import Comment, Generic, Keyword, Name, Number, Operator, String


def get_app_data_dir():
    override = os.environ.get("SERVER_MANAGER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "ServerManager"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ServerManager"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "server-manager"


def get_server_password(server_id):
    return keyring.get_password(SERVER_PASSWORD_SERVICE, server_id) or ""


def set_server_password(server_id, password):
    if password:
        keyring.set_password(SERVER_PASSWORD_SERVICE, server_id, password)
        return
    try:
        keyring.delete_password(SERVER_PASSWORD_SERVICE, server_id)
    except keyring.errors.PasswordDeleteError:
        pass


RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = get_app_data_dir()
SERVERS_FILE = APP_DIR / "servers.json"
SETTINGS_FILE = APP_DIR / "settings.json"
SERVER_PASSWORD_SERVICE = "ServerManager"
LOCALES_DIR = RESOURCE_DIR / "locales"
APP_KNOWN_HOSTS = APP_DIR / "known_hosts"
HOST_KEYS_LOCK = threading.Lock()
ROOT_SFTP_COMMAND = (
    "for path in /usr/lib/openssh/sftp-server /usr/lib/ssh/sftp-server "
    "/usr/libexec/openssh/sftp-server /usr/libexec/sftp-server /usr/lib/sftp-server; "
    "do if [ -x \"$path\" ]; then exec \"$path\"; fi; done; "
    "echo 'sftp-server executable not found' >&2; exit 127"
)
MAX_REMOTE_EDIT_SIZE = 2 * 1024 * 1024
MAX_PREVIEW_SIZE = 512 * 1024 * 1024
PREFERRED_LOCALE_ORDER = ("kk", "ru", "en", "ar", "zh", "fr", "es")


def load_locales():
    locales = {}
    for locale_path in LOCALES_DIR.glob("*.json"):
        try:
            locale = json.loads(locale_path.read_text(encoding="utf-8"))
            code = locale["code"]
            name = locale["name"]
            translations = locale["translations"]
            if isinstance(code, str) and isinstance(name, str) and isinstance(translations, dict):
                locales[code] = {"name": name, "translations": translations}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
    if "en" not in locales:
        locales["en"] = {"name": "English", "translations": {}}
    ordered = {
        code: locales[code]
        for code in PREFERRED_LOCALE_ORDER
        if code in locales
    }
    ordered.update({code: locales[code] for code in sorted(locales) if code not in ordered})
    return ordered


LOCALES = load_locales()


def load_language():
    try:
        code = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("language", "en")
    except (OSError, json.JSONDecodeError, AttributeError):
        code = "en"
    return code if isinstance(code, str) and code in LOCALES else "en"


CURRENT_LANGUAGE = load_language()


def translate(message, **values):
    translated = LOCALES.get(CURRENT_LANGUAGE, {}).get("translations", {}).get(message, message)
    return translated.format(**values) if values else translated


def save_language(code):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps({"language": code}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ask_yes_no(parent, title, message):
    dialog = QMessageBox(QMessageBox.Icon.Question, title, message, QMessageBox.StandardButton.NoButton, parent)
    yes_button = dialog.addButton(translate("Yes"), QMessageBox.ButtonRole.YesRole)
    dialog.addButton(translate("No"), QMessageBox.ButtonRole.NoRole)
    dialog.setDefaultButton(yes_button)
    dialog.exec()
    return dialog.clickedButton() is yes_button


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("Settings"))
        self.language_combo = QComboBox()
        for code, locale in LOCALES.items():
            self.language_combo.addItem(locale["name"], code)
        current_index = self.language_combo.findData(CURRENT_LANGUAGE)
        self.language_combo.setCurrentIndex(max(0, current_index))

        form = QFormLayout()
        form.addRow(translate("Interface language"), self.language_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(translate("Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(translate("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @property
    def language_code(self):
        return self.language_combo.currentData()


class TerminalScreen(pyte.Screen):
    def __init__(self, columns, lines):
        super().__init__(columns, lines)
        self.private_modes = set()

    def set_mode(self, *modes, **kwargs):
        super().set_mode(*modes, **kwargs)
        if kwargs.get("private"):
            self.private_modes.update(modes)

    def reset_mode(self, *modes, **kwargs):
        super().reset_mode(*modes, **kwargs)
        if kwargs.get("private"):
            self.private_modes.difference_update(modes)


class TerminalWidget(QWidget):
    resized = Signal(int, int)
    input_ready = Signal(str)

    COLORS = {
        "black": "#000000", "red": "#cd0000", "green": "#00cd00",
        "brown": "#cdcd00", "blue": "#0000ee", "magenta": "#cd00cd",
        "cyan": "#00cdcd", "white": "#e5e5e5", "brightblack": "#7f7f7f",
        "brightred": "#ff0000", "brightgreen": "#00ff00", "brightbrown": "#ffff00",
        "brightblue": "#5c5cff", "brightmagenta": "#ff00ff", "brightcyan": "#00ffff",
        "brightwhite": "#ffffff",
    }
    KEY_SEQUENCES = {
        Qt.Key.Key_Up: "A", Qt.Key.Key_Down: "B", Qt.Key.Key_Right: "C",
        Qt.Key.Key_Left: "D", Qt.Key.Key_Home: "H", Qt.Key.Key_End: "F",
    }
    TILDE_KEYS = {
        Qt.Key.Key_Insert: 2, Qt.Key.Key_Delete: 3, Qt.Key.Key_PageUp: 5,
        Qt.Key.Key_PageDown: 6, Qt.Key.Key_F5: 15, Qt.Key.Key_F6: 17,
        Qt.Key.Key_F7: 18, Qt.Key.Key_F8: 19, Qt.Key.Key_F9: 20,
        Qt.Key.Key_F10: 21, Qt.Key.Key_F11: 23, Qt.Key.Key_F12: 24,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("terminal")
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(300, 200)
        self.setFont(QFont("DejaVu Sans Mono", 12))
        self.metrics = QFontMetrics(self.font())
        self.cell_width = max(1, self.metrics.horizontalAdvance("M"))
        self.cell_height = max(1, self.metrics.lineSpacing())
        columns, lines = self.terminal_size()
        self.screen = TerminalScreen(columns, lines)
        self.stream = pyte.Stream(self.screen)
        self.selection_start = None
        self.selection_end = None
        self.selecting = False
        self.setStyleSheet("background: #0b1012; border: 1px solid #2b393b; border-radius: 5px;")

    def terminal_size(self):
        return (
            max(20, self.width() // self.cell_width),
            max(5, self.height() // self.cell_height),
        )

    def feed(self, data):
        self.stream.feed(data)
        self.update()

    def clear(self):
        columns, lines = self.terminal_size()
        self.screen = TerminalScreen(columns, lines)
        self.stream = pyte.Stream(self.screen)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        columns, lines = self.terminal_size()
        if columns != self.screen.columns or lines != self.screen.lines:
            self.screen.resize(lines=lines, columns=columns)
            self.resized.emit(columns, lines)
            self.update()

    def color(self, value, fallback):
        if value == "default":
            return QColor(fallback)
        value = self.COLORS.get(value, value)
        if isinstance(value, str) and len(value) == 6:
            try:
                int(value, 16)
                value = f"#{value}"
            except ValueError:
                pass
        color = QColor(value)
        return color if color.isValid() else QColor(fallback)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.fillRect(self.rect(), QColor("#0b1012"))
        painter.setFont(self.font())
        baseline_offset = self.metrics.ascent()

        for row_index in range(self.screen.lines):
            row = self.screen.buffer.get(row_index, {})
            y = row_index * self.cell_height
            for column_index, cell in row.items():
                x = column_index * self.cell_width
                if not cell.data:
                    continue
                foreground = self.color(cell.fg, "#f0f3f2")
                background = self.color(cell.bg, "#0b1012")
                if cell.reverse:
                    foreground, background = background, foreground
                if self.is_selected(row_index, column_index):
                    foreground, background = QColor("#ffffff"), QColor("#356b5b")
                character_width = max(1, wcswidth(cell.data))
                painter.fillRect(
                    x, y, self.cell_width * character_width, self.cell_height, background
                )
                if cell.data != " ":
                    font = QFont(self.font())
                    font.setBold(cell.bold)
                    font.setItalic(cell.italics)
                    painter.setFont(font)
                    painter.setPen(foreground)
                    painter.drawText(x, y + baseline_offset, cell.data)
                    if cell.underscore:
                        painter.drawLine(
                            x,
                            y + self.cell_height - 1,
                            x + self.cell_width * character_width,
                            y + self.cell_height - 1,
                        )

        cursor = self.screen.cursor
        if not cursor.hidden and 0 <= cursor.y < self.screen.lines:
            x = cursor.x * self.cell_width
            y = cursor.y * self.cell_height
            painter.fillRect(x, y + self.cell_height - 2, self.cell_width, 2, QColor("#79c99d"))

    def selection_bounds(self):
        if self.selection_start is None or self.selection_end is None:
            return None
        return sorted((self.selection_start, self.selection_end))

    def is_selected(self, row, column):
        bounds = self.selection_bounds()
        if bounds is None:
            return False
        start, end = bounds
        return start <= (row, column) <= end

    def selection_text(self):
        bounds = self.selection_bounds()
        if bounds is None:
            return ""
        (start_row, start_column), (end_row, end_column) = bounds
        selected_rows = []
        for row_index in range(start_row, end_row + 1):
            row = self.screen.buffer.get(row_index, {})
            first_column = start_column if row_index == start_row else 0
            last_column = end_column if row_index == end_row else self.screen.columns - 1
            text = "".join(
                row[column].data if column in row else " "
                for column in range(first_column, last_column + 1)
            )
            selected_rows.append(text.rstrip())
        return "\n".join(selected_rows)

    def selection_position(self, position):
        column = max(0, min(self.screen.columns - 1, position.x() // self.cell_width))
        row = max(0, min(self.screen.lines - 1, position.y() // self.cell_height))
        return row, column

    def copy_selection(self):
        text = self.selection_text()
        if text:
            QApplication.clipboard().setText(text)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        if key == Qt.Key.Key_C and control and shift:
            self.copy_selection()
            return

        if key == Qt.Key.Key_C and control and self.selection_text():
            self.copy_selection()
            return

        if (key == Qt.Key.Key_V and control and shift) or (
            key == Qt.Key.Key_Insert and shift
        ):
            text = QApplication.clipboard().text()
            if text:
                if 2004 in self.screen.private_modes:
                    text = f"\x1b[200~{text}\x1b[201~"
                self.input_ready.emit(text)
            return

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                self.input_ready.emit(chr(key - Qt.Key.Key_A + 1))
                return
            if key == Qt.Key.Key_Space:
                self.input_ready.emit("\x00")
                return

        if key in self.KEY_SEQUENCES:
            suffix = self.KEY_SEQUENCES[key]
            if 1 in self.screen.private_modes:
                self.input_ready.emit(f"\x1bO{suffix}")
            else:
                self.input_ready.emit(f"\x1b[{suffix}")
        elif key in self.TILDE_KEYS:
            self.input_ready.emit(f"\x1b[{self.TILDE_KEYS[key]}~")
        elif key == Qt.Key.Key_F1:
            self.input_ready.emit("\x1bOP")
        elif key == Qt.Key.Key_F2:
            self.input_ready.emit("\x1bOQ")
        elif key == Qt.Key.Key_F3:
            self.input_ready.emit("\x1bOR")
        elif key == Qt.Key.Key_F4:
            self.input_ready.emit("\x1bOS")
        elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            self.input_ready.emit("\r")
        elif key == Qt.Key.Key_Backspace:
            self.input_ready.emit("\x7f")
        elif key == Qt.Key.Key_Tab:
            self.input_ready.emit("\x1b[Z" if modifiers & Qt.KeyboardModifier.ShiftModifier else "\t")
        elif key == Qt.Key.Key_Escape:
            self.input_ready.emit("\x1b")
        elif event.text():
            self.input_ready.emit(event.text())
        else:
            super().keyPressEvent(event)

    def mouse_sequence(self, button, position, release=False):
        modes = self.screen.private_modes
        if not modes.intersection({1000, 1002, 1003}):
            return
        column = max(1, min(self.screen.columns, position.x() // self.cell_width + 1))
        row = max(1, min(self.screen.lines, position.y() // self.cell_height + 1))
        if 1006 in modes:
            suffix = "m" if release else "M"
            self.input_ready.emit(f"\x1b[<{button};{column};{row}{suffix}")
        else:
            code = min((3 if release else button) + 32, 255)
            self.input_ready.emit(f"\x1b[M{chr(code)}{chr(column + 32)}{chr(row + 32)}")

    def mousePressEvent(self, event):
        position = event.position().toPoint()
        modes = self.screen.private_modes
        tracking_mouse = bool(modes.intersection({1000, 1002, 1003}))
        if event.button() == Qt.MouseButton.LeftButton and (
            not tracking_mouse or event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.selection_start = self.selection_position(position)
            self.selection_end = self.selection_start
            self.selecting = True
            self.update()
            self.setFocus()
            return

        button = {
            Qt.MouseButton.LeftButton: 0,
            Qt.MouseButton.MiddleButton: 1,
            Qt.MouseButton.RightButton: 2,
        }.get(event.button())
        if button is not None:
            self.mouse_sequence(button, position)
        self.setFocus()

    def mouseMoveEvent(self, event):
        if self.selecting and event.buttons() & Qt.MouseButton.LeftButton:
            self.selection_end = self.selection_position(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.selecting and event.button() == Qt.MouseButton.LeftButton:
            self.selection_end = self.selection_position(event.position().toPoint())
            self.selecting = False
            self.update()
            return

        button = {
            Qt.MouseButton.LeftButton: 0,
            Qt.MouseButton.MiddleButton: 1,
            Qt.MouseButton.RightButton: 2,
        }.get(event.button())
        if button is not None:
            self.mouse_sequence(button, event.position().toPoint(), release=True)

    def wheelEvent(self, event):
        button = 64 if event.angleDelta().y() > 0 else 65
        self.mouse_sequence(button, event.position().toPoint())


class ServerDialog(QDialog):
    def __init__(self, server=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("Server"))
        self.setMinimumWidth(420)

        server = server or {}
        self.name_input = QLineEdit(server.get("name", ""))
        self.host_input = QLineEdit(server.get("host", ""))
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(server.get("port", 22))
        self.username_input = QLineEdit(server.get("username", ""))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText(
            translate("Leave empty to keep saved password") if server else translate("Optional")
        )
        self.clear_password_checkbox = QCheckBox(translate("Remove saved password"))
        self.clear_password_checkbox.setVisible(bool(server))
        self.key_input = QLineEdit(server.get("key_file", ""))
        self.key_input.setPlaceholderText(translate("Optional: ~/.ssh/id_ed25519"))

        browse_button = QPushButton(translate("Browse…"))
        browse_button.clicked.connect(self.choose_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_input)
        key_row.addWidget(browse_button)

        form = QFormLayout()
        form.addRow(translate("Server name"), self.name_input)
        form.addRow(translate("Host"), self.host_input)
        form.addRow(translate("Port"), self.port_input)
        form.addRow(translate("Username"), self.username_input)
        form.addRow(translate("Password"), self.password_input)
        form.addRow("", self.clear_password_checkbox)
        form.addRow(translate("SSH key"), key_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(translate("Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(translate("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def choose_key(self):
        path, _ = QFileDialog.getOpenFileName(self, translate("Select SSH key"))
        if path:
            self.key_input.setText(path)

    def server_data(self):
        return {
            "name": self.name_input.text().strip(),
            "host": self.host_input.text().strip(),
            "port": self.port_input.value(),
            "username": self.username_input.text().strip(),
            "password": self.password_input.text(),
            "clear_password": self.clear_password_checkbox.isChecked(),
            "key_file": self.key_input.text().strip(),
        }

    def accept(self):
        data = self.server_data()
        if not all((data["name"], data["host"], data["username"])):
            QMessageBox.warning(
                self,
                translate("Required fields are missing"),
                translate("Enter a name, host, and username."),
            )
            return
        super().accept()


class SSHSession(QThread):
    connected = Signal()
    progress = Signal(str)
    output = Signal(str)
    failed = Signal(str)
    disconnected = Signal()

    def __init__(self, server, password, columns, lines):
        super().__init__()
        self.server = server
        self.password = password
        self.columns = columns
        self.lines = lines
        self.pending_input = queue.Queue()
        self.stopping = False
        self.client = None
        self.channel = None

    def send(self, text):
        self.pending_input.put(("input", text))

    def resize(self, columns, lines):
        self.pending_input.put(("resize", (columns, lines)))

    def stop(self):
        self.stopping = True
        self.pending_input.put(None)
        if self.client:
            self.client.close()

    def run(self):
        try:
            self.progress.emit(translate("Initializing SSH client"))
            self.client = paramiko.SSHClient()
            self.progress.emit(translate("Loading known server keys"))
            self.client.load_system_host_keys()
            APP_DIR.mkdir(parents=True, exist_ok=True)
            if APP_KNOWN_HOSTS.is_file():
                self.client.load_host_keys(str(APP_KNOWN_HOSTS))
            self.client.set_missing_host_key_policy(AutoTrustHostKeyPolicy(self.progress))
            options = {
                "hostname": self.server["host"],
                "port": self.server["port"],
                "username": self.server["username"],
                "timeout": 12,
                "banner_timeout": 12,
                "auth_timeout": 12,
                "allow_agent": True,
                "look_for_keys": True,
            }
            if self.server["key_file"]:
                options["key_filename"] = str(Path(self.server["key_file"]).expanduser())
            if self.password:
                options["password"] = self.password
            self.progress.emit(translate(
                "Connecting to {host}:{port}",
                host=self.server["host"],
                port=self.server["port"],
            ))
            self.progress.emit(translate("Verifying server key and authenticating"))
            self.client.connect(**options)
            self.progress.emit(translate("Authentication successful; starting remote shell"))
            self.channel = self.client.invoke_shell(
                term="xterm-256color", width=self.columns, height=self.lines
            )
            self.channel.settimeout(0.1)
            self.progress.emit(translate("Terminal is ready"))
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            self.connected.emit()

            while not self.stopping and not self.channel.closed:
                try:
                    action = self.pending_input.get(timeout=0.03)
                    if action is None:
                        break
                    operation, payload = action
                    if operation == "input":
                        self.channel.sendall(payload.encode("utf-8"))
                    elif operation == "resize" and not self.channel.closed:
                        columns, lines = payload
                        self.channel.resize_pty(width=columns, height=lines)
                except queue.Empty:
                    pass

                if self.channel.recv_ready():
                    data = self.channel.recv(4096)
                    self.output.emit(decoder.decode(data))
        except Exception as error:
            if not self.stopping:
                self.failed.emit(str(error))
        finally:
            if self.client:
                self.client.close()
            self.disconnected.emit()


class AutoTrustHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, progress):
        self.progress = progress

    def missing_host_key(self, client, hostname, key):
        key_type = key.get_name()
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest())
        fingerprint = fingerprint.decode("ascii").rstrip("=")

        APP_DIR.mkdir(parents=True, exist_ok=True)
        with HOST_KEYS_LOCK:
            known_hosts = paramiko.HostKeys()
            if APP_KNOWN_HOSTS.is_file():
                known_hosts.load(str(APP_KNOWN_HOSTS))
            host_keys = known_hosts.lookup(hostname) or {}
            existing_key = host_keys.get(key_type)
            if existing_key and existing_key.asbytes() != key.asbytes():
                raise paramiko.BadHostKeyException(hostname, key, existing_key)
            if existing_key is None:
                known_hosts.add(hostname, key_type, key)
                known_hosts.save(str(APP_KNOWN_HOSTS))

        client.get_host_keys().add(hostname, key_type, key)
        if existing_key is None:
            self.progress.emit(translate(
                "New {key_type} key for {hostname} accepted and saved (SHA256:{fingerprint})",
                key_type=key_type,
                hostname=hostname,
                fingerprint=fingerprint,
            ))
        else:
            self.progress.emit(translate(
                "The {key_type} key for {hostname} matches the saved key",
                key_type=key_type,
                hostname=hostname,
            ))


class PygmentsHighlighter(QSyntaxHighlighter):
    TOKEN_COLORS = (
        (Comment, "#6a9955"),
        (Keyword, "#569cd6"),
        (Name.Function, "#dcdcaa"),
        (Name.Class, "#4ec9b0"),
        (Name.Builtin, "#4ec9b0"),
        (String, "#ce9178"),
        (Number, "#b5cea8"),
        (Operator, "#d4d4d4"),
        (Generic.Heading, "#4fc1ff"),
        (Generic.Emph, "#c586c0"),
        (Generic.Strong, "#dcdcaa"),
    )

    def __init__(self, document, filename):
        super().__init__(document)
        try:
            self.lexer = get_lexer_for_filename(filename)
        except Exception:
            self.lexer = TextLexer()
        self.formats = []
        for token, color in self.TOKEN_COLORS:
            text_format = QTextCharFormat()
            text_format.setForeground(QColor(color))
            if token in (Keyword, Name.Class, Generic.Strong):
                text_format.setFontWeight(QFont.Weight.Bold)
            self.formats.append((token, text_format))
        self.lines = []
        self.refreshing = False
        document.contentsChanged.connect(self.refresh_tokens)
        self.refresh_tokens()

    def refresh_tokens(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            text = self.document().toPlainText()
            self.lines = [[] for _ in range(text.count("\n") + 1)]
            line_number = 0
            column = 0
            for token, value in lex(text, self.lexer):
                pieces = value.splitlines(keepends=True)
                if not pieces and value:
                    pieces = [value]
                for piece in pieces:
                    visible = piece.rstrip("\r\n")
                    if visible:
                        text_format = next(
                            (fmt for parent, fmt in self.formats if token in parent), None
                        )
                        if text_format and line_number < len(self.lines):
                            self.lines[line_number].append(
                                (column, column + len(visible), text_format)
                            )
                    if len(piece) > len(visible):
                        line_number += piece.count("\n") or 1
                        column = 0
                    else:
                        column += len(piece)
            self.rehighlight()
        finally:
            self.refreshing = False

    def highlightBlock(self, text):
        block_number = self.currentBlock().blockNumber()
        if block_number >= len(self.lines):
            return
        for start, end, text_format in self.lines[block_number]:
            self.setFormat(start, end - start, text_format)


class RemoteTextEditor(QDialog):
    save_requested = Signal(str, object, bool)

    def __init__(self, entry, content, root_mode, parent=None):
        super().__init__(parent)
        self.entry = dict(entry)
        self.root_mode = root_mode
        self.saving = False
        self.setWindowTitle(translate("File editor — {name}", name=entry["name"]))
        self.resize(900, 650)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("remoteTextEditor")
        self.editor.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.editor.setFont(QFont("DejaVu Sans Mono", 11))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlainText(content)
        self.editor.document().setModified(False)
        self.highlighter = PygmentsHighlighter(
            self.editor.document(), entry["name"]
        )

        self.status_label = QLabel(translate("UTF-8 · Pygments"))
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        self.save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        self.button_box.button(QDialogButtonBox.StandardButton.Close).setText(
            translate("Close")
        )
        self.save_button.clicked.connect(self.save)
        self.button_box.rejected.connect(self.close)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.save)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.button_box)
        self.setStyleSheet("""
            QPlainTextEdit#remoteTextEditor {
                background: #101619; color: #e7eeed; border: 1px solid #354649;
                font-family: monospace; font-size: 13px; padding: 8px;
                selection-background-color: #31594f;
            }
        """)

    def save(self):
        if self.saving:
            return
        if not self.editor.document().isModified():
            self.status_label.setText(translate("No changes"))
            return
        content = self.editor.toPlainText()
        self.saving = True
        self.save_button.setEnabled(False)
        self.status_label.setText(translate("Saving to server…"))
        self.save_requested.emit(
            self.entry["path"],
            {
                "content": content,
                "expected_sha256": self.entry["sha256"],
            },
            self.root_mode,
        )

    def save_succeeded(self, attributes):
        self.entry.update(attributes)
        self.editor.document().setModified(False)
        self.saving = False
        self.save_button.setEnabled(True)
        self.status_label.setText(translate("Saved"))

    def save_failed(self, message):
        self.saving = False
        self.save_button.setEnabled(True)
        self.status_label.setText(translate("Save failed: {message}", message=message))

    def closeEvent(self, event):
        if self.saving:
            event.ignore()
            return
        if self.editor.document().isModified():
            confirmed = ask_yes_no(
                self,
                translate("Unsaved changes"),
                translate("Close the editor and discard unsaved changes?"),
            )
            if not confirmed:
                event.ignore()
                return
        event.accept()


class ScaledImageLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_source_pixmap(self, pixmap):
        self.source_pixmap = pixmap
        self.update_scaled_pixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scaled_pixmap()

    def update_scaled_pixmap(self):
        if not self.source_pixmap.isNull() and self.width() > 0 and self.height() > 0:
            self.setPixmap(self.source_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


class FilePreviewDialog(QDialog):
    def __init__(self, file_path, parent=None):
        super().__init__(parent)
        self.file_path = Path(file_path)
        self.setWindowTitle(translate("Preview — {name}", name=self.file_path.name))
        self.resize(1000, 720)
        self.player = None
        mime_type = mimetypes.guess_type(self.file_path.name)[0] or ""

        layout = QVBoxLayout(self)
        if mime_type == "application/pdf" or self.file_path.suffix.lower() == ".pdf":
            self.pdf_document = QPdfDocument(self)
            self.pdf_view = QPdfView(self)
            self.pdf_document.load(str(self.file_path))
            self.pdf_view.setDocument(self.pdf_document)
            layout.addWidget(self.pdf_view, 1)
            self.status_label = QLabel()
            self.pdf_document.statusChanged.connect(self.update_pdf_status)
            layout.addWidget(self.status_label)
            self.update_pdf_status()
        elif mime_type.startswith("image/"):
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            image = ScaledImageLabel()
            pixmap = QPixmap(str(self.file_path))
            if pixmap.isNull():
                image.setText(translate("Could not open image"))
            else:
                image.set_source_pixmap(pixmap)
            scroll_area.setWidget(image)
            layout.addWidget(scroll_area, 1)
        elif mime_type.startswith(("audio/", "video/")):
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            if mime_type.startswith("video/"):
                self.video_widget = QVideoWidget()
                self.player.setVideoOutput(self.video_widget)
                layout.addWidget(self.video_widget, 1)

            controls = QHBoxLayout()
            self.play_button = QPushButton(translate("Play"))
            self.play_button.clicked.connect(self.toggle_playback)
            self.position_slider = QSlider(Qt.Orientation.Horizontal)
            self.position_slider.sliderMoved.connect(self.player.setPosition)
            self.time_label = QLabel(translate("00:00 / 00:00"))
            controls.addWidget(self.play_button)
            controls.addWidget(self.position_slider, 1)
            controls.addWidget(self.time_label)
            layout.addLayout(controls)
            self.player.positionChanged.connect(self.update_position)
            self.player.durationChanged.connect(self.update_duration)
            self.player.playbackStateChanged.connect(self.update_play_button)
            self.player.errorOccurred.connect(self.show_media_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.file_path)))
        else:
            layout.addWidget(QLabel(translate(
                "System viewer: {name}", name=self.file_path.name
            )))
            open_button = QPushButton(translate("Open with system application"))
            open_button.clicked.connect(self.open_external)
            layout.addWidget(open_button)

    def update_pdf_status(self):
        if not hasattr(self, "status_label"):
            return
        if self.pdf_document.status() == QPdfDocument.Status.Ready:
            self.status_label.setText(translate(
                "{pages} pages", pages=self.pdf_document.pageCount()
            ))
        elif self.pdf_document.status() == QPdfDocument.Status.Error:
            self.status_label.setText(translate(
                "PDF error: {error}", error=self.pdf_document.error()
            ))
        else:
            self.status_label.setText(translate("Loading PDF…"))

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def update_play_button(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText(translate("Pause") if playing else translate("Play"))

    def update_position(self, position):
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self.update_time_label(position, self.player.duration())

    def update_duration(self, duration):
        self.position_slider.setRange(0, duration)
        self.update_time_label(self.player.position(), duration)

    def update_time_label(self, position, duration):
        def time_text(milliseconds):
            seconds = milliseconds // 1000
            return f"{seconds // 60:02}:{seconds % 60:02}"

        self.time_label.setText(f"{time_text(position)} / {time_text(duration)}")

    def show_media_error(self, error, message):
        self.play_button.setText(translate("Error: {message}", message=message or error))
        self.play_button.setEnabled(False)

    def open_external(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.file_path)))

    def closeEvent(self, event):
        if self.player:
            self.player.stop()
        event.accept()


class SFTPTask(QThread):
    completed = Signal(str, object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, transport, operation, path, payload=None, root_mode=False, sudo_password=None):
        super().__init__()
        self.transport = transport
        self.operation = operation
        self.path = path
        self.payload = payload
        self.root_mode = root_mode
        self.sudo_password = sudo_password
        self.succeeded = False
        self.requires_sudo_password = False

    def open_sftp(self):
        if not self.root_mode:
            return paramiko.SFTPClient.from_transport(self.transport)

        channel = self.transport.open_session()
        channel.settimeout(15)
        sudo_options = "-n" if self.sudo_password is None else "-S -p ''"
        command = f"sudo {sudo_options} sh -c {shlex.quote(ROOT_SFTP_COMMAND)}"
        channel.exec_command(command)
        if self.sudo_password is not None:
            channel.sendall(self.sudo_password.encode("utf-8") + b"\n")

        try:
            sftp = paramiko.SFTPClient(channel)
            channel.settimeout(None)
            return sftp
        except Exception as error:
            error_output = bytearray()
            try:
                while channel.recv_stderr_ready():
                    error_output.extend(channel.recv_stderr(4096))
            except Exception:
                pass
            message = error_output.decode("utf-8", errors="replace").strip()
            lowered_message = message.lower()
            if self.sudo_password is None and (
                "a password is required" in lowered_message
                or "password is required" in lowered_message
            ):
                self.requires_sudo_password = True
                return None
            raise RuntimeError(message or str(error))

    def run(self):
        sftp = None
        preview_path = None
        try:
            sftp = self.open_sftp()
            if sftp is None:
                return
            if self.operation == "list":
                remote_path = sftp.normalize(self.path)
                entries = []
                for attributes in sftp.listdir_attr(remote_path):
                    mode = attributes.st_mode or 0
                    entries.append({
                        "name": attributes.filename,
                        "path": posixpath.join(remote_path, attributes.filename),
                        "is_dir": stat.S_ISDIR(mode),
                        "size": attributes.st_size or 0,
                        "mtime": attributes.st_mtime,
                    })
                self.succeeded = True
                self.completed.emit(self.operation, (remote_path, entries))
            elif self.operation == "read":
                attributes = sftp.stat(self.path)
                if attributes.st_size and attributes.st_size > MAX_REMOTE_EDIT_SIZE:
                    raise ValueError(translate("File exceeds 2 MB; download it to view"))
                with sftp.open(self.path, "rb") as remote_file:
                    content = remote_file.read(MAX_REMOTE_EDIT_SIZE + 1)
                if len(content) > MAX_REMOTE_EDIT_SIZE:
                    raise ValueError(translate("File exceeds 2 MB; download it to view"))
                if b"\0" in content:
                    raise ValueError(translate("Binary files cannot be opened in the text editor"))
                encoding = "utf-8-sig" if content.startswith(codecs.BOM_UTF8) else "utf-8"
                text = content.decode(encoding)
                result = {
                    "entry": {
                        "name": posixpath.basename(self.path),
                        "path": self.path,
                        "is_dir": False,
                        "size": len(content),
                        "mtime": attributes.st_mtime or 0,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "encoding": encoding,
                    },
                    "content": text,
                }
                self.succeeded = True
                self.completed.emit(self.operation, result)
            elif self.operation == "write":
                with sftp.open(self.path, "rb") as remote_file:
                    current_content = remote_file.read(MAX_REMOTE_EDIT_SIZE + 1)
                current_sha256 = hashlib.sha256(current_content).hexdigest()
                if current_sha256 != self.payload["expected_sha256"]:
                    raise RuntimeError(translate(
                        "The file changed on the server; reload it before saving"
                    ))
                encoding = self.payload.get("encoding", "utf-8")
                encoded_content = self.payload["content"].encode(encoding)
                with sftp.open(self.path, "wb") as remote_file:
                    remote_file.write(encoded_content)
                    remote_file.flush()
                attributes = sftp.stat(self.path)
                result = {
                    "size": attributes.st_size or len(encoded_content),
                    "mtime": attributes.st_mtime or 0,
                    "sha256": hashlib.sha256(encoded_content).hexdigest(),
                }
                self.succeeded = True
                self.completed.emit(self.operation, result)
            elif self.operation == "mkdir":
                sftp.mkdir(self.path)
                self.succeeded = True
                self.completed.emit(self.operation, self.path)
            elif self.operation == "delete":
                if self.payload:
                    sftp.rmdir(self.path)
                else:
                    sftp.remove(self.path)
                self.succeeded = True
                self.completed.emit(self.operation, self.path)
            elif self.operation == "upload":
                sftp.put(
                    self.path,
                    self.payload,
                    callback=lambda transferred, total: self.progress.emit(transferred, total),
                )
                self.succeeded = True
                self.completed.emit(self.operation, self.payload)
            elif self.operation == "download":
                sftp.get(
                    self.path,
                    self.payload,
                    callback=lambda transferred, total: self.progress.emit(transferred, total),
                )
                self.succeeded = True
                self.completed.emit(self.operation, self.path)
            elif self.operation == "preview":
                attributes = sftp.stat(self.path)
                if attributes.st_size and attributes.st_size > MAX_PREVIEW_SIZE:
                    raise ValueError(translate(
                        "File exceeds 512 MB and cannot be previewed"
                    ))
                preview_directory = Path(tempfile.mkdtemp(prefix="server-manager-preview-"))
                preview_path = preview_directory / Path(posixpath.basename(self.path)).name
                sftp.get(
                    self.path,
                    str(preview_path),
                    callback=lambda transferred, total: self.progress.emit(transferred, total),
                )
                self.succeeded = True
                self.completed.emit(
                    self.operation,
                    {"path": str(preview_path), "directory": str(preview_directory)},
                )
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            if preview_path and not self.succeeded:
                try:
                    preview_path.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    preview_path.parent.rmdir()
                except OSError:
                    pass


class RemoteFileManager(QWidget):
    operation_requested = Signal(str, str, object)
    file_open_requested = Signal(object)
    preview_requested = Signal(object)
    root_mode_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.remote_path = "."
        self.connected = False
        self.busy = False

        self.path_input = QLineEdit(self.remote_path)
        self.path_input.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.path_input.setPlaceholderText(translate("Remote path"))
        self.path_input.returnPressed.connect(self.navigate_to_path)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_input, 1)
        path_row.addWidget(self.make_button("SP_ArrowUp", translate("Go to parent directory"), self.go_up))
        path_row.addWidget(self.make_button("SP_BrowserReload", translate("Refresh directory"), self.refresh))

        actions = QHBoxLayout()
        actions.addWidget(self.make_button("SP_DialogOpenButton", translate("Upload file to server"), self.upload_file))
        actions.addWidget(self.make_button("SP_DialogSaveButton", translate("Download selected file"), self.download_file))
        actions.addWidget(self.make_button("SP_FileDialogContentsView", translate("Preview selected file"), self.preview_file))
        actions.addWidget(self.make_button("SP_FileIcon", translate("Open and edit text file"), self.edit_file))
        actions.addWidget(self.make_button("SP_FileDialogNewFolder", translate("Create directory"), self.create_directory))
        actions.addWidget(self.make_button("SP_TrashIcon", translate("Delete selected item"), self.delete_item))
        self.root_checkbox = QCheckBox("Root / sudo")
        self.root_checkbox.setToolTip(translate("Manage files through sudo"))
        if sys.platform == "win32":
            self.root_checkbox.setEnabled(False)
            self.root_checkbox.setToolTip(translate("Root / sudo is unavailable on Windows"))
        self.root_checkbox.toggled.connect(self.root_mode_changed.emit)
        actions.addWidget(self.root_checkbox)
        actions.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            translate("Name"), translate("Size"), translate("Modified"), translate("Type")
        ])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.currentItemChanged.connect(
            lambda current, previous: self.update_controls()
        )
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 4):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(self.open_item)

        self.status_label = QLabel(translate("Connect to a server to browse files"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(path_row)
        layout.addLayout(actions)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status_label)
        self.tool_buttons = [
            button for row in (path_row, actions)
            for button in (row.itemAt(index).widget() for index in range(row.count()))
            if isinstance(button, QToolButton)
        ]
        self.update_controls()

    def make_button(self, icon_name, tooltip, callback):
        button = QToolButton()
        icon_enum = getattr(QStyle.StandardPixmap, icon_name, None)
        if icon_enum is not None:
            button.setIcon(self.style().standardIcon(icon_enum))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setProperty("action_id", icon_name)
        button.clicked.connect(callback)
        return button

    def update_controls(self):
        for button in self.tool_buttons:
            button.setEnabled(self.connected and not self.busy)
        self.path_input.setEnabled(self.connected and not self.busy)
        self.root_checkbox.setEnabled(self.connected and not self.busy)
        selected = self.selected_entry()
        self.tree.setEnabled(self.connected and not self.busy)
        for button in self.tool_buttons:
            if button.property("action_id") in (
                "SP_DialogSaveButton",
                "SP_FileDialogContentsView",
                "SP_FileIcon",
            ):
                button.setEnabled(
                    self.connected
                    and not self.busy
                    and selected is not None
                    and not selected["is_dir"]
                )

    def set_connected(self, connected):
        self.connected = connected
        if not connected:
            self.root_checkbox.blockSignals(True)
            self.root_checkbox.setChecked(False)
            self.root_checkbox.blockSignals(False)
            self.tree.clear()
            self.status_label.setText(translate("Connect to a server to browse files"))
        self.update_controls()

    def set_root_mode(self, enabled):
        self.root_checkbox.blockSignals(True)
        self.root_checkbox.setChecked(enabled)
        self.root_checkbox.blockSignals(False)
        self.update_controls()

    def set_busy(self, busy):
        self.busy = busy
        self.update_controls()

    def set_status(self, message):
        self.status_label.setText(message)

    def selected_entry(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def refresh(self):
        if self.connected and not self.busy:
            self.operation_requested.emit("list", self.remote_path, None)
            self.set_status(translate("Reading {path}…", path=self.remote_path))

    def navigate_to_path(self):
        path = self.path_input.text().strip()
        if path:
            self.remote_path = path
            self.refresh()

    def go_up(self):
        parent = posixpath.dirname(self.remote_path.rstrip("/")) or "/"
        self.remote_path = parent
        self.path_input.setText(parent)
        self.refresh()

    def show_directory(self, path, entries):
        self.remote_path = path
        self.path_input.setText(path)
        self.tree.clear()
        entries.sort(key=lambda entry: (not entry["is_dir"], entry["name"].casefold()))
        for entry in entries:
            item = QTreeWidgetItem([
                entry["name"],
                "" if entry["is_dir"] else self.format_size(entry["size"]),
                self.format_time(entry["mtime"]),
                translate("Directory") if entry["is_dir"] else translate("File"),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            icon_name = "SP_DirIcon" if entry["is_dir"] else "SP_FileIcon"
            icon_enum = getattr(QStyle.StandardPixmap, icon_name, None)
            if icon_enum is not None:
                item.setIcon(0, self.style().standardIcon(icon_enum))
            self.tree.addTopLevelItem(item)
        self.set_status(translate("{count} items", count=len(entries)))
        self.update_controls()

    def format_size(self, size):
        value = float(size)
        for source_unit, divider in (("B", 1), ("KB", 1024), ("MB", 1024**2), ("GB", 1024**3), ("TB", 1024**4)):
            unit = translate(source_unit)
            if value < 1024 or divider == 1024**4:
                return f"{value:.0f} {unit}" if divider == 1 else f"{value:.1f} {unit}"
            value /= 1024

    def format_time(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M") if timestamp else ""

    def open_item(self, item, column):
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry["is_dir"]:
            self.remote_path = entry["path"]
            self.path_input.setText(self.remote_path)
            self.refresh()
        else:
            mime_type = mimetypes.guess_type(entry["name"])[0] or ""
            if (
                mime_type.startswith(("image/", "audio/", "video/"))
                or mime_type == "application/pdf"
                or mime_type.startswith("application/vnd.")
                or entry["name"].lower().endswith((".pdf", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp"))
            ):
                self.preview_file()
            else:
                self.edit_file()

    def edit_file(self):
        entry = self.selected_entry()
        if not entry or entry["is_dir"]:
            return
        self.file_open_requested.emit(entry)
        self.set_status(translate("Opening {name}…", name=entry["name"]))

    def upload_file(self):
        local_path, _ = QFileDialog.getOpenFileName(self, translate("Upload file to server"))
        if local_path:
            filename = Path(local_path).name
            existing = self.tree.findItems(
                filename, Qt.MatchFlag.MatchExactly, 0
            )
            if existing:
                confirmed = ask_yes_no(
                    self,
                    translate("Overwrite file"),
                    translate(
                        "“{filename}” already exists. Overwrite it?",
                        filename=filename,
                    ),
                )
                if not confirmed:
                    return
            remote_path = posixpath.join(self.remote_path, filename)
            self.operation_requested.emit("upload", local_path, remote_path)
            self.set_status(translate("Uploading {name}…", name=filename))

    def download_file(self):
        entry = self.selected_entry()
        if not entry or entry["is_dir"]:
            return
        local_path, _ = QFileDialog.getSaveFileName(
            self, translate("Download file"), entry["name"]
        )
        if local_path:
            self.operation_requested.emit("download", entry["path"], local_path)
            self.set_status(translate("Downloading {name}…", name=entry["name"]))

    def preview_file(self):
        entry = self.selected_entry()
        if not entry or entry["is_dir"]:
            return
        self.preview_requested.emit(entry)
        self.set_status(translate("Preparing preview of {name}…", name=entry["name"]))

    def create_directory(self):
        name, accepted = QInputDialog.getText(
            self, translate("New directory"), translate("Directory name:")
        )
        name = name.strip()
        if accepted and name and name not in (".", "..") and "/" not in name:
            self.operation_requested.emit("mkdir", posixpath.join(self.remote_path, name), None)
            self.set_status(translate("Creating directory {name}…", name=name))

    def delete_item(self):
        entry = self.selected_entry()
        if not entry:
            return
        confirmed = ask_yes_no(
            self,
            translate("Delete item"),
            translate("Delete “{name}” from the server?", name=entry["name"]),
        )
        if confirmed:
            self.operation_requested.emit("delete", entry["path"], entry["is_dir"])
            self.set_status(translate("Deleting {name}…", name=entry["name"]))


class MainWindow(QMainWindow):
    focus_terminal_requested = Signal(str)
    ssh_connected_requested = Signal(str, object)
    ssh_progress_requested = Signal(str, str)
    ssh_output_requested = Signal(str, str)
    ssh_failed_requested = Signal(str, str)
    ssh_disconnected_requested = Signal(str, object)
    ssh_finished_requested = Signal(str, object)
    sftp_completed_requested = Signal(str, object, str, object)
    sftp_failed_requested = Signal(str, object, str)
    sftp_progress_requested = Signal(str, int, int)
    sftp_finished_requested = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.focus_terminal_requested.connect(
            self.focus_terminal, Qt.ConnectionType.QueuedConnection
        )
        self.ssh_connected_requested.connect(
            self.on_connected, Qt.ConnectionType.QueuedConnection
        )
        self.ssh_progress_requested.connect(
            self.write_connection_log, Qt.ConnectionType.QueuedConnection
        )
        self.ssh_output_requested.connect(
            self.write_output, Qt.ConnectionType.QueuedConnection
        )
        self.ssh_failed_requested.connect(
            self.on_error, Qt.ConnectionType.QueuedConnection
        )
        self.ssh_disconnected_requested.connect(
            self.on_disconnected, Qt.ConnectionType.QueuedConnection
        )
        self.ssh_finished_requested.connect(
            self.on_session_finished, Qt.ConnectionType.QueuedConnection
        )
        self.sftp_completed_requested.connect(
            self.on_sftp_completed, Qt.ConnectionType.QueuedConnection
        )
        self.sftp_failed_requested.connect(
            self.on_sftp_failed, Qt.ConnectionType.QueuedConnection
        )
        self.sftp_progress_requested.connect(
            self.on_sftp_progress, Qt.ConnectionType.QueuedConnection
        )
        self.sftp_finished_requested.connect(
            self.on_sftp_finished, Qt.ConnectionType.QueuedConnection
        )
        self.setWindowTitle(translate("VikhmanSSH"))
        self.resize(1100, 720)
        screen = QApplication.primaryScreen()
        if screen:
            frame = self.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())
        self.servers = self.load_servers()
        self.server_ids = [server["id"] for server in self.servers]
        self.sessions = {}
        self.session_states = {server_id: "offline" for server_id in self.server_ids}
        self.terminals = {}
        self.terminal_pages = {}
        self.connection_logs = {}
        self.connection_log_buttons = {}
        self.file_managers = {}
        self.file_tasks = {}
        self.preview_directories = []
        self.preview_dialogs = []
        self.pending_editor_saves = {}
        self.pending_removal = set()
        self.current_server_id = None

        self.server_list = QListWidget()
        self.server_list.currentRowChanged.connect(self.select_server)

        add_button = QPushButton(f"＋  {translate("Add")}")
        edit_button = QPushButton(translate("Edit"))
        remove_button = QPushButton(translate("Delete"))
        add_button.clicked.connect(self.add_server)
        edit_button.clicked.connect(self.edit_server)
        remove_button.clicked.connect(self.remove_server)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(270)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 20, 16, 16)
        sidebar_layout.addWidget(QLabel(translate("SERVERS")))
        sidebar_layout.addWidget(self.server_list, 1)
        sidebar_layout.addWidget(add_button)
        server_actions = QHBoxLayout()
        server_actions.addWidget(edit_button)
        server_actions.addWidget(remove_button)
        sidebar_layout.addLayout(server_actions)

        self.server_title = QLabel(translate("Select a server"))
        self.server_title.setObjectName("serverTitle")
        self.server_subtitle = QLabel(translate("SSH connection and remote shell"))
        self.status_label = QLabel(translate("Disconnected"))
        self.status_label.setObjectName("status")
        self.connect_button = QPushButton(translate("Connect"))
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.settings_button = QPushButton(translate("Settings"))
        self.settings_button.clicked.connect(self.open_settings)

        heading = QHBoxLayout()
        heading.addWidget(self.server_title, 1)
        heading.addWidget(self.status_label)
        heading.addWidget(self.connect_button)
        heading.addWidget(self.settings_button)

        self.terminal_stack = QStackedWidget()
        self.empty_terminal = TerminalWidget()
        self.terminal_stack.addWidget(self.empty_terminal)
        self.terminal = self.empty_terminal

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 24)
        content_layout.addLayout(heading)
        content_layout.addWidget(self.server_subtitle)
        content_layout.addSpacing(16)
        content_layout.addWidget(self.terminal_stack, 1)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(sidebar)
        body.addWidget(content, 1)
        central = QWidget()
        central.setLayout(body)
        self.setCentralWidget(central)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #101619; color: #e7eeed; font-size: 13px; }
            QWidget#sidebar { background: #182124; border-right: 1px solid #2a3739; }
            QLabel { color: #aab8b7; }
            QLabel#serverTitle { color: #f1f5f3; font-size: 22px; font-weight: 600; }
            QLabel#status { color: #e2b870; padding: 7px 10px; }
            QListWidget, QLineEdit {
                background: #0b1012; border: 1px solid #2b393b; border-radius: 5px;
                padding: 9px; selection-background-color: #31594f;
            }
            QPlainTextEdit#connectionLog {
                background: #0b1012; color: #c5d8d0; border: 1px solid #2b393b;
                border-radius: 5px; padding: 7px; font-family: monospace; font-size: 12px;
            }
            QListWidget { padding: 4px; }
            QListWidget::item { padding: 10px 8px; border-radius: 4px; }
            QListWidget::item:selected { background: #28443e; color: #f3f7f5; }
            QPushButton {
                background: #263336; border: 1px solid #354649; border-radius: 5px;
                padding: 8px 12px; color: #e7eeed;
            }
            QPushButton:hover { background: #334447; }
            QPushButton:disabled { color: #71807e; background: #202a2c; }
            QPushButton#primary { background: #39745e; border-color: #47896f; font-weight: 600; }
            QPushButton#primary:hover { background: #43866c; }
        """)

        self.refresh_server_list()
        if self.servers:
            self.server_list.setCurrentRow(0)

    def load_servers(self):
        try:
            data = json.loads(SERVERS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
        except (OSError, json.JSONDecodeError):
            return []
        identifiers = set()
        changed = False
        for server in data:
            server_id = server.get("id")
            if not server_id or server_id in identifiers:
                server_id = uuid.uuid4().hex
                server["id"] = server_id
                changed = True
            identifiers.add(server_id)
        if changed:
            self.save_servers(data)
        return data

    def open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        language_code = dialog.language_code
        if language_code == CURRENT_LANGUAGE:
            return
        save_language(language_code)
        QMessageBox.information(
            self,
            translate("Language changed"),
            translate("The application will restart to apply the selected language."),
        )
        self.close()
        os.execv(
            sys.executable,
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        )

    def save_servers(self, servers=None):
        SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SERVERS_FILE.write_text(
            json.dumps(servers if servers is not None else self.servers, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def refresh_server_list(self):
        selected_id = self.current_server_id
        previous_row = self.selected_index()
        self.server_list.blockSignals(True)
        self.server_list.clear()
        self.server_ids = []
        for server in self.servers:
            server_id = server["id"]
            self.server_list.addItem(server["name"])
            self.server_ids.append(server_id)
            self.update_connection_indicator(server_id)
        self.server_list.blockSignals(False)

        if self.server_ids:
            if selected_id in self.server_ids:
                row = self.server_ids.index(selected_id)
            else:
                row = min(max(previous_row, 0), len(self.server_ids) - 1)
            self.server_list.setCurrentRow(row)
            self.select_server(row)
        else:
            self.server_list.setCurrentRow(-1)
            self.select_server(-1)

    def selected_index(self):
        return self.server_list.currentRow()

    def selected_server_id(self):
        index = self.selected_index()
        return self.server_ids[index] if 0 <= index < len(self.server_ids) else None

    def ensure_terminal(self, server_id):
        terminal = self.terminals.get(server_id)
        if terminal is None:
            page = QTabWidget()
            terminal_page = QWidget()
            terminal_layout = QVBoxLayout(terminal_page)
            terminal_layout.setContentsMargins(0, 0, 0, 0)
            terminal_layout.setSpacing(8)

            log_header = QHBoxLayout()
            log_header.addWidget(QLabel(translate("CONNECTION LOG")), 1)
            log_button = QPushButton(translate("Show log"))
            log_button.setCheckable(True)
            log_button.toggled.connect(
                lambda visible, key=server_id: self.set_connection_log_visible(key, visible)
            )
            log_header.addWidget(log_button)

            connection_log = QPlainTextEdit()
            connection_log.setObjectName("connectionLog")
            connection_log.setReadOnly(True)
            connection_log.setMaximumBlockCount(300)
            connection_log.setFixedHeight(120)
            connection_log.hide()

            terminal = TerminalWidget()
            terminal.input_ready.connect(
                lambda text, key=server_id: self.send_input(key, text)
            )
            terminal.resized.connect(
                lambda columns, lines, key=server_id: self.resize_terminal(key, columns, lines)
            )
            terminal_layout.addLayout(log_header)
            terminal_layout.addWidget(connection_log)
            terminal_layout.addWidget(terminal, 1)

            file_manager = RemoteFileManager()
            file_manager.operation_requested.connect(
                lambda operation, path, payload, key=server_id: self.run_sftp_operation(
                    key,
                    operation,
                    path,
                    payload,
                    root_mode=file_manager.root_checkbox.isChecked(),
                )
            )
            file_manager.file_open_requested.connect(
                lambda entry, key=server_id: self.open_remote_file(key, entry)
            )
            file_manager.preview_requested.connect(
                lambda entry, key=server_id: self.preview_remote_file(key, entry)
            )
            file_manager.root_mode_changed.connect(
                lambda enabled, key=server_id: self.set_sftp_root_mode(key, enabled)
            )
            page.addTab(terminal_page, translate("Terminal"))
            page.addTab(file_manager, translate("Files"))

            self.terminals[server_id] = terminal
            self.terminal_pages[server_id] = page
            self.connection_logs[server_id] = connection_log
            self.connection_log_buttons[server_id] = log_button
            self.file_managers[server_id] = file_manager
            self.terminal_stack.addWidget(page)
        return terminal

    def set_connection_log_visible(self, server_id, visible):
        connection_log = self.connection_logs.get(server_id)
        log_button = self.connection_log_buttons.get(server_id)
        if connection_log:
            connection_log.setVisible(visible)
        if log_button:
            log_button.setText(
                translate("Hide log") if visible else translate("Show log")
            )

    def write_connection_log(self, server_id, message):
        connection_log = self.connection_logs.get(server_id)
        if connection_log:
            timestamp = QTime.currentTime().toString("HH:mm:ss")
            connection_log.appendPlainText(f"[{timestamp}] {message}")

    def update_connection_indicator(self, server_id):
        colors = {
            "offline": "#657371",
            "connecting": "#e2b870",
            "connected": "#79c99d",
            "disconnecting": "#e2b870",
            "error": "#e58e82",
        }
        try:
            row = self.server_ids.index(server_id)
        except ValueError:
            return
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors.get(self.session_states.get(server_id), "#657371")))
        painter.drawEllipse(2, 2, 8, 8)
        painter.end()
        self.server_list.item(row).setIcon(pixmap)

    def select_server(self, index):
        if 0 <= index < len(self.servers):
            server = self.servers[index]
            server_id = server["id"]
            self.current_server_id = server_id
            self.server_title.setText(server["name"])
            self.server_subtitle.setText(
                f"{server['username']}@{server['host']}:{server['port']}"
            )
            self.terminal = self.ensure_terminal(server_id)
            self.terminal_stack.setCurrentWidget(self.terminal_pages[server_id])
        else:
            self.current_server_id = None
            self.terminal = self.empty_terminal
            self.terminal_stack.setCurrentWidget(self.empty_terminal)
            self.server_title.setText(translate("Select a server"))
            self.server_subtitle.setText(translate("SSH connection and remote shell"))
        self.update_selected_status()

    def update_selected_status(self):
        server_id = self.current_server_id
        if server_id is None:
            self.status_label.setText(translate("Disconnected"))
            self.status_label.setStyleSheet("color: #e2b870; padding: 7px 10px;")
            self.connect_button.setText(translate("Connect"))
            self.connect_button.setEnabled(False)
            return

        state = self.session_states.get(server_id, "offline")
        labels = {
            "offline": (translate("Disconnected"), translate("Connect")),
            "connecting": (translate("Connecting…"), translate("Abort")),
            "connected": (translate("Connected"), translate("Disconnect")),
            "disconnecting": (translate("Disconnecting…"), translate("Disconnecting…")),
            "error": (translate("Connection error"), translate("Connect")),
        }
        status_text, button_text = labels.get(state, labels["offline"])
        status_color = {
            "connected": "#79c99d",
            "error": "#e58e82",
        }.get(state, "#e2b870")
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet(f"color: {status_color}; padding: 7px 10px;")
        self.connect_button.setText(button_text)
        has_session = self.sessions.get(server_id) is not None
        can_disconnect = state in ("connecting", "connected")
        can_connect = state in ("offline", "error") and not has_session
        self.connect_button.setEnabled(can_connect or can_disconnect)

    def add_server(self):
        dialog = ServerDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            server = dialog.server_data()
            server["id"] = uuid.uuid4().hex
            try:
                password = server.pop("password")
                server.pop("clear_password")
                if password:
                    set_server_password(server["id"], password)
                server["password_saved"] = bool(password)
            except Exception as error:
                QMessageBox.warning(
                    self,
                    translate("Secure password storage unavailable"),
                    translate("Could not save the password securely: {error}", error=error),
                )
                return
            self.servers.append(server)
            self.session_states[server["id"]] = "offline"
            self.save_servers()
            self.refresh_server_list()
            self.server_list.setCurrentRow(len(self.servers) - 1)

    def edit_server(self):
        index = self.selected_index()
        if index < 0:
            return
        server = self.servers[index]
        dialog = ServerDialog(server, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            server = dialog.server_data()
            server_id = self.servers[index]["id"]
            password = server.pop("password")
            clear_password = server.pop("clear_password")
            had_password = self.servers[index].get("password_saved", False)
            if password or (clear_password and had_password):
                try:
                    set_server_password(server_id, password)
                except Exception as error:
                    QMessageBox.warning(
                        self,
                        translate("Secure password storage unavailable"),
                        translate("Could not save the password securely: {error}", error=error),
                    )
                    return
            server["password_saved"] = bool(password) or (had_password and not clear_password)
            server["id"] = server_id
            self.servers[index] = server
            self.save_servers()
            self.refresh_server_list()
            self.server_list.setCurrentRow(index)

    def remove_server(self):
        index = self.selected_index()
        if index < 0:
            return
        confirmed = ask_yes_no(
            self,
            translate("Delete server"),
            translate("Delete “{name}”?", name=self.servers[index]["name"]),
        )
        if confirmed:
            server_id = self.servers[index]["id"]
            if self.servers[index].get("password_saved", False):
                try:
                    set_server_password(server_id, "")
                except Exception as error:
                    QMessageBox.warning(
                        self,
                        translate("Secure password storage unavailable"),
                        translate("Could not remove the password securely: {error}", error=error),
                    )
                    return
            session = self.sessions.get(server_id)
            if session:
                self.pending_removal.add(server_id)
                self.session_states[server_id] = "disconnecting"
                session.stop()
            else:
                self.remove_terminal(server_id)
                self.session_states.pop(server_id, None)
            self.servers.pop(index)
            self.save_servers()
            self.refresh_server_list()

    def connect_server(self):
        server_id = self.current_server_id
        if server_id is None or self.sessions.get(server_id) is not None:
            return
        server = next(server for server in self.servers if server["id"] == server_id)
        try:
            password = get_server_password(server_id) if server.get("password_saved", False) else ""
        except Exception as error:
            QMessageBox.warning(
                self,
                translate("Secure password storage unavailable"),
                translate("Could not read the password securely: {error}", error=error),
            )
            return

        terminal = self.ensure_terminal(server_id)
        terminal.clear()
        connection_log = self.connection_logs[server_id]
        connection_log.clear()
        self.connection_log_buttons[server_id].setChecked(True)
        self.write_connection_log(server_id, translate("Connection started"))
        columns, lines = terminal.terminal_size()
        session = SSHSession(server, password, columns, lines)
        self.sessions[server_id] = session
        self.session_states[server_id] = "connecting"
        session.connected.connect(
            lambda key=server_id, worker=session: self.ssh_connected_requested.emit(key, worker)
        )
        session.progress.connect(
            lambda message, key=server_id: self.ssh_progress_requested.emit(key, message)
        )
        session.output.connect(
            lambda text, key=server_id: self.ssh_output_requested.emit(key, text)
        )
        session.failed.connect(
            lambda message, key=server_id: self.ssh_failed_requested.emit(key, message)
        )
        session.disconnected.connect(
            lambda key=server_id, worker=session: self.ssh_disconnected_requested.emit(key, worker)
        )
        session.finished.connect(
            lambda key=server_id, worker=session: self.ssh_finished_requested.emit(key, worker)
        )
        session.finished.connect(session.deleteLater)
        session.start()
        self.update_connection_indicator(server_id)
        self.update_selected_status()

    def toggle_connection(self):
        if self.current_server_id is None:
            return
        if self.session_states.get(self.current_server_id) in ("connecting", "connected"):
            self.disconnect_server()
        else:
            self.connect_server()

    def on_connected(self, server_id, session):
        if self.sessions.get(server_id) is not session:
            return
        self.session_states[server_id] = "connected"
        self.update_connection_indicator(server_id)
        if self.current_server_id == server_id:
            self.update_selected_status()
            self.focus_terminal_requested.emit(server_id)
        self.write_connection_log(server_id, translate("SSH connection established"))
        file_manager = self.file_managers.get(server_id)
        if file_manager:
            file_manager.set_connected(True)
            file_manager.refresh()

    def focus_terminal(self, server_id):
        if self.current_server_id != server_id or self.session_states.get(server_id) != "connected":
            return
        terminal = self.terminals.get(server_id)
        page = self.terminal_pages.get(server_id)
        if not terminal or not page:
            return
        self.terminal = terminal
        page.setCurrentIndex(0)
        self.terminal_stack.setCurrentWidget(page)
        self.activateWindow()
        self.raise_()
        terminal.setFocus(Qt.FocusReason.OtherFocusReason)

    def disconnect_server(self):
        server_id = self.current_server_id
        session = self.sessions.get(server_id)
        if session:
            self.session_states[server_id] = "disconnecting"
            self.update_connection_indicator(server_id)
            self.update_selected_status()
            session.stop()

    def send_input(self, server_id, text):
        session = self.sessions.get(server_id)
        if not session or not session.isRunning():
            return
        session.send(text)

    def resize_terminal(self, server_id, columns, lines):
        session = self.sessions.get(server_id)
        if session and session.isRunning():
            session.resize(columns, lines)

    def write_output(self, server_id, text):
        terminal = self.terminals.get(server_id)
        if terminal:
            terminal.feed(text)

    def set_sftp_root_mode(self, server_id, enabled):
        manager = self.file_managers.get(server_id)
        if not manager:
            return
        if self.session_states.get(server_id) != "connected":
            manager.set_root_mode(False)
            manager.set_status(translate("No active SSH connection"))
            return
        manager.set_status(
            translate("Checking sudo and reading directory…")
            if enabled
            else translate("Switching back to regular user…")
        )
        self.run_sftp_operation(server_id, "list", manager.remote_path, root_mode=enabled)

    def run_sftp_operation(self, server_id, operation, path, payload=None, root_mode=False, sudo_password=None):
        manager = self.file_managers.get(server_id)
        session = self.sessions.get(server_id)
        if not manager or not session or self.session_states.get(server_id) != "connected":
            if manager:
                manager.set_status(translate("No active SSH connection"))
            return
        existing_task = self.file_tasks.get(server_id)
        if existing_task and existing_task.isRunning():
            return
        transport = session.client.get_transport() if session.client else None
        if not transport or not transport.is_active():
            manager.set_status(translate("SSH connection is unavailable"))
            return

        task = SFTPTask(
            transport,
            operation,
            path,
            payload,
            root_mode=root_mode,
            sudo_password=sudo_password,
        )
        self.file_tasks[server_id] = task
        manager.set_busy(True)
        task.completed.connect(
            lambda kind, result, key=server_id, worker=task: self.sftp_completed_requested.emit(
                key, worker, kind, result
            )
        )
        task.failed.connect(
            lambda message, key=server_id, worker=task: self.sftp_failed_requested.emit(
                key, worker, message
            )
        )
        task.progress.connect(
            lambda transferred, total, key=server_id: self.sftp_progress_requested.emit(
                key, transferred, total
            )
        )
        task.finished.connect(
            lambda key=server_id, worker=task: self.sftp_finished_requested.emit(key, worker)
        )
        task.finished.connect(task.deleteLater)
        task.start()

    def on_sftp_completed(self, server_id, task, operation, result):
        if self.file_tasks.get(server_id) is not task:
            return
        manager = self.file_managers.get(server_id)
        if not manager:
            return
        if operation == "list":
            remote_path, entries = result
            manager.show_directory(remote_path, entries)
        elif operation == "read":
            editor = RemoteTextEditor(
                result["entry"], result["content"], task.root_mode, self
            )
            editor.save_requested.connect(
                lambda path, payload, root_mode, key=server_id, dialog=editor: self.save_remote_file(
                    key, dialog, path, payload, root_mode
                )
            )
            editor.show()
            editor.raise_()
            editor.activateWindow()
        elif operation == "write":
            editor = self.pending_editor_saves.pop((server_id, task.path), None)
            if editor:
                editor.save_succeeded(result)
            manager.set_status(translate("Saved: {name}", name=posixpath.basename(task.path)))
        elif operation == "upload":
            manager.set_status(translate("File uploaded: {name}", name=posixpath.basename(result)))
        elif operation == "download":
            manager.set_status(translate("File downloaded: {name}", name=posixpath.basename(result)))
        elif operation == "preview":
            preview_path = Path(result["path"])
            self.preview_directories.append(Path(result["directory"]))
            mime_type = mimetypes.guess_type(preview_path.name)[0] or ""
            if (
                mime_type.startswith(("image/", "audio/", "video/"))
                or mime_type == "application/pdf"
                or preview_path.suffix.lower() == ".pdf"
            ):
                viewer = FilePreviewDialog(preview_path, self)
                self.preview_dialogs.append(viewer)
                viewer.finished.connect(
                    lambda result, dialog=viewer: self.preview_dialogs.remove(dialog)
                    if dialog in self.preview_dialogs
                    else None
                )
                viewer.show()
                viewer.raise_()
                viewer.activateWindow()
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(preview_path))):
                QMessageBox.warning(
                    self,
                    translate("No viewer available"),
                    translate(
                        "Could not open {name} with a system application.",
                        name=preview_path.name,
                    ),
                )
            manager.set_status(translate("Preview: {name}", name=preview_path.name))
        elif operation == "mkdir":
            manager.set_status(translate("Directory created: {name}", name=posixpath.basename(result)))
        elif operation == "delete":
            manager.set_status(translate("Deleted: {name}", name=posixpath.basename(result)))

    def on_sftp_failed(self, server_id, task, message):
        if self.file_tasks.get(server_id) is task:
            manager = self.file_managers.get(server_id)
            if manager:
                manager.set_status(translate("SFTP error: {message}", message=message))
            if task.operation == "write":
                editor = self.pending_editor_saves.pop((server_id, task.path), None)
                if editor:
                    editor.save_failed(message)

    def open_remote_file(self, server_id, entry):
        manager = self.file_managers.get(server_id)
        if manager:
            manager.set_status(translate("Opening {name}…", name=entry["name"]))
            self.run_sftp_operation(
                server_id,
                "read",
                entry["path"],
                root_mode=manager.root_checkbox.isChecked(),
            )

    def preview_remote_file(self, server_id, entry):
        manager = self.file_managers.get(server_id)
        if manager:
            manager.set_status(translate(
                "Downloading for preview: {name}…", name=entry["name"]
            ))
            self.run_sftp_operation(
                server_id,
                "preview",
                entry["path"],
                root_mode=manager.root_checkbox.isChecked(),
            )

    def save_remote_file(self, server_id, editor, path, payload, root_mode):
        task = self.file_tasks.get(server_id)
        if task and task.isRunning():
            editor.save_failed(translate("Wait for the current file operation to finish"))
            return
        self.pending_editor_saves[(server_id, path)] = editor
        self.run_sftp_operation(
            server_id,
            "write",
            path,
            payload,
            root_mode=root_mode,
        )

    def on_sftp_progress(self, server_id, transferred, total):
        manager = self.file_managers.get(server_id)
        if manager and total:
            percent = transferred * 100 // total
            manager.set_status(translate("File transfer: {percent}%", percent=percent))

    def on_sftp_finished(self, server_id, task):
        if self.file_tasks.get(server_id) is not task:
            return
        self.file_tasks.pop(server_id, None)
        manager = self.file_managers.get(server_id)
        if manager:
            manager.set_busy(False)
            if task.requires_sudo_password:
                server = next(
                    (item for item in self.servers if item["id"] == server_id),
                    {"name": translate("server")},
                )
                password, accepted = QInputDialog.getText(
                    self,
                    translate("Sudo password required"),
                    translate(
                        "Enter the sudo password for {name} to manage files as root",
                        name=server["name"],
                    ),
                    QLineEdit.EchoMode.Password,
                )
                if accepted and password:
                    self.run_sftp_operation(
                        server_id,
                        task.operation,
                        task.path,
                        task.payload,
                        root_mode=True,
                        sudo_password=password,
                    )
                else:
                    manager.set_root_mode(False)
                    manager.set_status(translate("Root access cancelled"))
                    if task.operation == "write":
                        editor = self.pending_editor_saves.pop(
                            (server_id, task.path), None
                        )
                        if editor:
                            editor.save_failed(translate("Sudo password request cancelled"))
                return
            if task.succeeded and task.operation in ("mkdir", "delete", "upload"):
                manager.refresh()

    def on_error(self, server_id, message):
        self.session_states[server_id] = "error"
        self.write_connection_log(server_id, translate("SSH error: {message}", message=message))
        self.update_connection_indicator(server_id)
        if self.current_server_id == server_id:
            self.update_selected_status()

    def on_disconnected(self, server_id, session):
        if self.sessions.get(server_id) is not session:
            return
        terminal = self.terminals.get(server_id)
        if terminal:
            terminal.clear()
        file_manager = self.file_managers.get(server_id)
        if file_manager:
            file_manager.set_connected(False)
        if self.session_states.get(server_id) != "error":
            self.session_states[server_id] = "offline"
            self.write_connection_log(server_id, translate("SSH session disconnected"))
        self.update_connection_indicator(server_id)
        if self.current_server_id == server_id:
            self.update_selected_status()

    def on_session_finished(self, server_id, session):
        if self.sessions.get(server_id) is session:
            self.sessions.pop(server_id, None)
        if server_id in self.pending_removal:
            self.pending_removal.discard(server_id)
            self.remove_terminal(server_id)
            self.session_states.pop(server_id, None)
        if self.current_server_id == server_id:
            self.update_selected_status()

    def remove_terminal(self, server_id):
        self.terminals.pop(server_id, None)
        self.connection_logs.pop(server_id, None)
        self.connection_log_buttons.pop(server_id, None)
        self.file_managers.pop(server_id, None)
        page = self.terminal_pages.pop(server_id, None)
        if page:
            self.terminal_stack.removeWidget(page)
            page.deleteLater()

    def closeEvent(self, event):
        sessions = [session for session in self.sessions.values() if session]
        file_tasks = list(self.file_tasks.values())
        for session in sessions:
            session.stop()
        for task in file_tasks:
            task.wait(1500)
        for session in sessions:
            session.wait(1500)
        for directory in self.preview_directories:
            shutil.rmtree(directory, ignore_errors=True)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("VikhmanSSH")
    app.setApplicationDisplayName("VikhmanSSH")
    app.setOrganizationName("Dmitriy Vikhman")
    app.setStyle("Fusion")
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft
        if CURRENT_LANGUAGE == "ar"
        else Qt.LayoutDirection.LeftToRight
    )
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()