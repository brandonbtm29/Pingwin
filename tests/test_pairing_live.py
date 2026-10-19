"""End-to-end pairing with two real, temporary SyncThing instances on this machine.

Skipped unless PINGWIN_SYNCTHING_EXE points at a syncthing executable. Uses its own ports and
folders, so it doesn't touch the SyncThing you normally run.
"""

import os
import shutil
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pingwin.config import load_config
from pingwin.pairing import Syncthing, accept, create_invite, join

EXE = os.environ.get("PINGWIN_SYNCTHING_EXE")
pytestmark = pytest.mark.skipif(not EXE, reason="set PINGWIN_SYNCTHING_EXE to run")
ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_instance(home: Path):
    subprocess.run([EXE, "generate", "--home", str(home), "--no-port-probing"], check=True, capture_output=True)
    gui_port, listen_port = free_port(), free_port()
    cfg = home / "config.xml"
    tree = ET.parse(cfg)
    root = tree.getroot()
    gui = root.find("gui")
    gui.set("tls", "false")
    gui.find("address").text = f"127.0.0.1:{gui_port}"
    opts = root.find("options")
    for tag, value in {
        "listenAddress": f"tcp://127.0.0.1:{listen_port}",
        "globalAnnounceEnabled": "false",
        "localAnnounceEnabled": "false",
        "relaysEnabled": "false",
        "natEnabled": "false",
        "startBrowser": "false",
        "urAccepted": "-1",
        "autoUpgradeIntervalH": "0",
        "crashReportingEnabled": "false",
    }.items():
        for el in opts.findall(tag):
            opts.remove(el)
        ET.SubElement(opts, tag).text = value
    # Don't create the default "Sync" folder in the test home.
    for f in root.findall("folder"):
        root.remove(f)
    tree.write(cfg)
    proc = subprocess.Popen(
        [EXE, "serve", "--home", str(home), "--no-browser", "--no-restart", "--no-upgrade"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    st = Syncthing.from_config(cfg)
    for _ in range(60):
        if st.ping():
            break
        time.sleep(0.5)
    else:
        stop(proc)
        pytest.fail("temporary SyncThing didn't start")
    return proc, st, f"tcp://127.0.0.1:{listen_port}"


def test_invite_join_accept_syncs_settings(tmp_path, monkeypatch):
    main_settings = tmp_path / "main-settings"
    main_settings.mkdir()
    shutil.copy(ROOT / "config.example.toml", main_settings / "config.toml")
    (main_settings / "secrets.env").write_text("DISCORD_TOKEN=test-token\n", encoding="utf-8")
    backup_settings = tmp_path / "backup-settings"
    monkeypatch.setenv("PINGWIN_DATA", str(main_settings))
    monkeypatch.delenv("PINGWIN_CONFIG", raising=False)

    proc_a, main_st, main_addr = start_instance(tmp_path / "st-main")
    proc_b, backup_st, backup_addr = start_instance(tmp_path / "st-backup")
    try:
        invite, notes = create_invite(main_st, main_settings)
        assert "Added your settings folder" in notes[0]
        reply = join(backup_st, invite, backup_settings, "TEST-BACKUP", main_addresses=[main_addr])
        accept_notes = accept(main_st, reply, main_settings, backup_addresses=[backup_addr])
        assert any("Shared the settings folder" in n for n in accept_notes)
        assert "TEST-BACKUP" in load_config(require_token=False).failover.pcs

        secrets, config = backup_settings / "secrets.env", backup_settings / "config.toml"
        for _ in range(120):
            if secrets.exists() and config.exists() and "TEST-BACKUP" in config.read_text(encoding="utf-8"):
                break
            time.sleep(1)
        else:
            pytest.fail("settings never synced to the backup")
        assert "test-token" in secrets.read_text(encoding="utf-8")
        # Running the steps again is harmless.
        create_invite(main_st, main_settings)
        accept(main_st, reply, main_settings, backup_addresses=[backup_addr])
    finally:
        for p in (proc_a, proc_b):
            stop(p)


def stop(proc):
    """Stop SyncThing and the child process it starts."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
