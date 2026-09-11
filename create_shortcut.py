#################### START OF FILE: create_shortcut.py ####################

"""
MrCoopersScreenShare - Source Shortcut Creator
Creates desktop and local shortcuts to launch `sender.py` directly from source
using pythonw.exe (windowless execution on Windows) or python3 (on Linux with KWin D-Bus permissions).
"""

import os
import re
import shutil
import subprocess
import sys


def get_pythonw_executable() -> str:
    """Finds pythonw.exe for silent/windowless execution on Windows."""
    current_exe = sys.executable
    dir_name = os.path.dirname(current_exe)

    c1 = os.path.join(dir_name, "pythonw.exe")
    if os.path.exists(c1):
        return c1

    c2 = re.sub(r"python\.exe$", "pythonw.exe", current_exe, flags=re.IGNORECASE)
    if os.path.exists(c2):
        return c2

    for prefix in [sys.exec_prefix, sys.base_exec_prefix]:
        c3 = os.path.join(prefix, "pythonw.exe")
        if os.path.exists(c3):
            return c3
        c3_scripts = os.path.join(prefix, "Scripts", "pythonw.exe")
        if os.path.exists(c3_scripts):
            return c3_scripts

    which_path = shutil.which("pythonw.exe") or shutil.which("pythonw")
    if which_path and os.path.exists(which_path):
        return which_path

    pyw_path = shutil.which("pyw.exe") or shutil.which("pyw")
    if pyw_path and os.path.exists(pyw_path):
        return pyw_path

    return current_exe


def generate_icon_files(project_root: str) -> tuple[str, str]:
    """Generates icon.ico and icon.png in the project directory if they do not exist."""
    ico_path = os.path.join(project_root, "icon.ico")
    png_path = os.path.join(project_root, "icon.png")

    if not os.path.exists(ico_path) or not os.path.exists(png_path):
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QColor, QPainter, QPixmap
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)

            pix = QPixmap(64, 64)
            pix.fill(Qt.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.Antialiasing, True)

            painter.setBrush(QColor("#0078d4"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(4, 4, 56, 56, 14, 14)

            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(14, 15, 36, 24, 4, 4)

            painter.setBrush(QColor("#1a1e29"))
            painter.drawRect(18, 19, 28, 16)

            painter.setBrush(QColor("#ffffff"))
            painter.drawRect(29, 41, 6, 4)
            painter.drawRoundedRect(22, 45, 20, 3, 1, 1)

            painter.end()

            pix.save(png_path, "PNG")
            pix.save(ico_path, "ICO")
            print(f"[INFO] Generated application icon: {ico_path}")
        except Exception as e:
            print(f"[WARNING] Could not generate icon file: {e}")

    return ico_path, png_path


def main():
    project_root = os.path.dirname(os.path.abspath(__file__))
    target_script = os.path.join(project_root, "sender.py")
    app_name = "MrCoopersScreenShare Sender"

    if not os.path.exists(target_script):
        print(f"[ERROR] Target script not found at '{target_script}'.")
        sys.exit(1)

    ico_path, png_path = generate_icon_files(project_root)

    if sys.platform == "win32":
        python_exe = get_pythonw_executable()
        icon_path = ico_path if os.path.exists(ico_path) else png_path
        create_windows_shortcuts(app_name, python_exe, target_script, project_root, icon_path)

    elif sys.platform.startswith("linux"):
        python_exe = sys.executable
        icon_path = png_path if os.path.exists(png_path) else ico_path
        create_linux_shortcuts(app_name, python_exe, target_script, project_root, icon_path)
    else:
        print(f"[ERROR] Unsupported operating system: {sys.platform}")


def create_windows_shortcuts(
    app_name: str,
    python_exe: str,
    target_script: str,
    working_dir: str,
    icon_path: str,
):
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    targets = [
        os.path.join(working_dir, f"{app_name}.lnk"),
        os.path.join(desktop_dir, f"{app_name}.lnk") if os.path.exists(desktop_dir) else None,
    ]

    def ps_quote(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    for shortcut_path in filter(None, targets):
        if os.path.exists(shortcut_path):
            try:
                os.remove(shortcut_path)
            except Exception:
                pass

        ps_command = f"""
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut({ps_quote(shortcut_path)})
        $Shortcut.TargetPath = {ps_quote(python_exe)}
        $Shortcut.Arguments = {ps_quote(f'"{target_script}"')}
        $Shortcut.WorkingDirectory = {ps_quote(working_dir)}
        """

        if os.path.exists(icon_path):
            ps_command += f"\n$Shortcut.IconLocation = {ps_quote(icon_path)}"

        ps_command += "\n$Shortcut.Save()"

        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_command], check=True)
            print(f"[SUCCESS] Windows Shortcut created: {shortcut_path}")
        except Exception as e:
            print(f"[ERROR] Failed to create shortcut at '{shortcut_path}': {e}")


def create_linux_shortcuts(
    app_name: str,
    python_exe: str,
    target_script: str,
    working_dir: str,
    icon_path: str,
):
    apps_dir = os.path.expanduser("~/.local/share/applications")
    desktop_dir = os.path.expanduser("~/Desktop")
    os.makedirs(apps_dir, exist_ok=True)

    targets = [
        os.path.join(working_dir, "mrcoopers-screenshare-sender.desktop"),
        os.path.join(apps_dir, "mrcoopers-screenshare-sender.desktop"),
        os.path.join(desktop_dir, "mrcoopers-screenshare-sender.desktop") if os.path.exists(desktop_dir) else None,
    ]

    desktop_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={app_name}
Comment=MrCoopersScreenShare Sender (Python Source)
Exec="{python_exe}" "{target_script}"
Path={working_dir}
Icon={icon_path if os.path.exists(icon_path) else 'video-display'}
Terminal=false
StartupNotify=true
Categories=Utility;Network;
X-KDE-DBUS-Restricted-Interfaces=org.kde.kwin.Screenshot,org.kde.KWin.ScreenShot2
"""

    for shortcut_path in filter(None, targets):
        try:
            with open(shortcut_path, "w", encoding="utf-8") as f:
                f.write(desktop_entry)

            st = os.stat(shortcut_path)
            os.chmod(shortcut_path, st.st_mode | 0o111)
            print(f"[SUCCESS] Linux .desktop shortcut created: {shortcut_path}")
        except Exception as e:
            print(f"[ERROR] Failed to create .desktop entry at '{shortcut_path}': {e}")


if __name__ == "__main__":
    main()