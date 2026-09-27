from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import atomic_write


def systemd_quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Service paths cannot contain newlines")
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
        + '"'
    )


def linux_unit(home: Path, executable: str) -> str:
    args = [executable, "-m", "citrus_agent", "--home", str(home), "run"]
    return "\n".join(
        [
            "[Unit]",
            "Description=Citrus messenger PC agent",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "ExecStart=" + " ".join(map(systemd_quote, args)),
            "Restart=on-failure",
            "RestartSec=15",
            "TimeoutStopSec=45",
            "KillMode=control-group",
            "UMask=0077",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def windows_task(home: Path, executable: str, user: str) -> str:
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", namespace)

    def add(parent, tag, text=None, **attrs):
        node = ET.SubElement(parent, f"{{{namespace}}}{tag}", attrs)
        node.text = text
        return node

    task = ET.Element(f"{{{namespace}}}Task", {"version": "1.2"})
    trigger = add(add(task, "Triggers"), "LogonTrigger")
    add(trigger, "Enabled", "true")
    add(trigger, "UserId", user)
    principal = add(add(task, "Principals"), "Principal", id="Author")
    add(principal, "UserId", user)
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")
    settings = add(task, "Settings")
    for key, value in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "StartWhenAvailable": "true",
        "ExecutionTimeLimit": "PT0S",
    }.items():
        add(settings, key, value)
    restart = add(settings, "RestartOnFailure")
    add(restart, "Interval", "PT1M")
    add(restart, "Count", "3")
    action = add(add(task, "Actions", Context="Author"), "Exec")
    add(action, "Command", executable)
    add(
        action,
        "Arguments",
        subprocess.list2cmdline(
            [
                "-m",
                "citrus_agent",
                "--home",
                str(home),
                "run",
            ]
        ),
    )
    return ET.tostring(task, encoding="unicode", xml_declaration=False)


def manage_service(action: str, home: Path):
    if sys.platform == "linux":
        unit = Path.home() / ".config/systemd/user/citrus-agent.service"
        if action == "install":
            atomic_write(unit, linux_unit(home, sys.executable))
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", "citrus-agent"], check=True)
        elif action == "uninstall":
            subprocess.run(["systemctl", "--user", "disable", "--now", "citrus-agent"], check=True)
            unit.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        else:
            subprocess.run(["systemctl", "--user", action, "citrus-agent"], check=True)
    elif sys.platform == "win32":
        name = "CitrusAgent"
        if action == "install":
            user = os.environ.get("USERDOMAIN", ".") + "\\" + getpass.getuser()
            executable = Path(sys.executable).with_name("pythonw.exe")
            if not executable.exists():
                executable = Path(sys.executable)
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "task.xml"
                path.write_text(windows_task(home, str(executable), user), encoding="utf-16")
                subprocess.run(
                    ["schtasks.exe", "/Create", "/TN", name, "/XML", str(path), "/F"], check=True
                )
            subprocess.run(["schtasks.exe", "/Run", "/TN", name], check=True)
        else:
            verb = {"uninstall": "/Delete", "start": "/Run", "stop": "/End", "status": "/Query"}[
                action
            ]
            if action == "uninstall":
                subprocess.run(["schtasks.exe", "/End", "/TN", name], check=False)
            command = ["schtasks.exe", verb, "/TN", name]
            if action == "uninstall":
                command.append("/F")
            subprocess.run(command, check=True)
    else:
        raise ValueError("Background services support Ubuntu and Windows; use 'run' on other OSes")
