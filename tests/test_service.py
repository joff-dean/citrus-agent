import xml.etree.ElementTree as ET
from pathlib import Path

from citrus_agent.service import linux_unit, windows_task


def test_systemd_paths_with_spaces_and_specifiers():
    unit = linux_unit(Path("/home/a b/100%/$data"), "/a b/python")
    assert '"/a b/python"' in unit
    assert "100%%/$$data" in unit
    assert "KillMode=control-group" in unit


def test_windows_task_current_user_and_quoted_paths():
    xml = windows_task(
        Path("C:/Users/이름/Test & Data"), "C:/Program Files/Python/python.exe", "DOMAIN\\user"
    )
    root = ET.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:LogonType", ns).text == "InteractiveToken"
    assert root.find(".//t:RunLevel", ns).text == "LeastPrivilege"
    assert '"C:' in root.find(".//t:Arguments", ns).text
    assert root.find(".//t:ExecutionTimeLimit", ns).text == "PT0S"
