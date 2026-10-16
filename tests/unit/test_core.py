"""
Unit tests for winshell.core - the salt-ssh Windows shell integration.

paramiko and scp are stubbed so the tests run without the real clients
installed; the Connection behavior is exercised through fakes.
"""

import importlib
import logging
import sys
import types
import zipfile

import pytest
import salt.exceptions


class _FakeSSHClient:
    def __init__(self):
        self.connected = False

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, host, username, key_filename):  # pylint: disable=unused-argument
        if host == "badhost":
            raise OSError("connection refused")
        self.connected = True

    def close(self):
        self.connected = False


paramiko_stub = types.ModuleType("paramiko")
paramiko_stub.client = types.ModuleType("paramiko.client")
paramiko_stub.client.SSHClient = _FakeSSHClient
paramiko_stub.AutoAddPolicy = object
scp_stub = types.ModuleType("scp")
scp_stub.SCPClient = object


@pytest.fixture
def core():
    sys.modules.setdefault("paramiko", paramiko_stub)
    sys.modules.setdefault("scp", scp_stub)
    return importlib.import_module("saltext.winshell.core")


@pytest.fixture
def fake_key(tmp_path):
    key = tmp_path / "salt-ssh.key"
    key.write_text("not a real key")
    return str(key)


class RecordingStdout:
    def __init__(self, data):
        self._data = data if isinstance(data, bytes) else data.encode()

    def read(self):
        return self._data


def test_env_version_matches_deployed_zips(core):
    assert core.ENV_VERSION == "3008"
    shell = core.Shell({}, "win2012dev", user="admin", winrm=True)
    core.set_winvars(shell)
    assert shell.python_env_map["AMD64"] == "Salt-Env-3008-AMD64-Py3.zip"
    assert shell.python_env_map["x86"] == "Salt-Env-3008-x86-Py3.zip"


def test_set_winvars_paths(core):
    shell = core.Shell({}, "win2012dev", user="admin", winrm=True)
    core.set_winvars(shell)
    assert shell.thin_dir == "c:/saltremote/thin"
    assert shell.python_dir == "c:/saltremote/bin"
    assert shell.python_saltwinshell == "/extra/salt/saltwinshell"


def test_gen_shim_embeds_code(core):
    shim = core.gen_shim("QUJD")
    assert 'exec(base64.b64decode("""QUJD"""))' in shim


def test_get_target_shim_file_uses_windows_paths(core):
    shell = core.Shell({}, "win2012dev", user="admin", winrm=True)
    core.set_winvars(shell)
    path = core.get_target_shim_file(shell, "shim.py")
    assert path == "c:\\saltremote\\bin\\shim.py"


def test_connection_requires_existing_key(core, tmp_path):
    with pytest.raises(salt.exceptions.SaltConfigurationError):
        core.Connection("host", "user", key=str(tmp_path / "missing.key"))


@pytest.fixture
def connected_connection(core, fake_key):
    conn = core.Connection("goodhost", "admin", key=fake_key)
    conn.conn = _FakeSSHClient()
    assert conn.connect() is True
    return conn


def test_cmd_exec_shim_return_codes(connected_connection):
    connected_connection.conn.exec_command = lambda cmd: (
        None,
        RecordingStdout("deploy\nmore"),
        RecordingStdout(""),
    )
    _, _, code = connected_connection.cmd_exec("shim deploy")
    assert code == 11

    connected_connection.conn.exec_command = lambda cmd: (
        None,
        RecordingStdout("ext_mods\n"),
        RecordingStdout(""),
    )
    _, _, code = connected_connection.cmd_exec("shim ext_mods")
    assert code == 13


def test_cmd_exec_err_makes_failure(connected_connection):
    connected_connection.conn.exec_command = lambda cmd: (
        None,
        RecordingStdout("output"),
        RecordingStdout("some error"),
    )
    _, _, code = connected_connection.cmd_exec("run something")
    assert code == 1


def test_cmd_exec_warning_is_not_failure(connected_connection):
    connected_connection.conn.exec_command = lambda cmd: (
        None,
        RecordingStdout("output"),
        RecordingStdout("WARNING: ignore me"),
    )
    _, _, code = connected_connection.cmd_exec("run something")
    assert code == 0


def test_cmd_exec_connect_failure(core, fake_key):
    conn = core.Connection("badhost", "admin", key=fake_key)
    _, err, code = conn.cmd_exec("anything")
    assert code == 1
    assert "Unable to connect to badhost" in err


def test_shell_send_raises_after_retries(core, monkeypatch, connected_connection):
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)
    shell = core.Shell({}, "goodhost", user="admin", winrm=True)
    shell._conn = connected_connection

    def failing_send(_local, _remote):
        return None, "scp boom", 1

    monkeypatch.setattr(connected_connection, "send", failing_send)
    with pytest.raises(salt.exceptions.SaltException):
        shell.send("c:/local.zip", "c:/saltremote/thin/bin.zip")
    assert shell._conn is None


def test_shell_exec_cmd_connection_failure(core, fake_key):
    shell = core.Shell({}, "badhost", user="admin", priv=fake_key, winrm=True)
    _, err, code = shell.exec_cmd("dir")
    assert code == 1
    assert "Unable to connect to badhost" in err


def test_deploy_python_expands_archive_with_force(core, fake_key, tmp_path):
    # the real Salt-Env zips are optional artifacts not carried in the
    # repository - build a minimal fake zip for the deployment path
    python_env = tmp_path / "Salt-Env-3008-AMD64-Py3.zip"
    with zipfile.ZipFile(python_env, "w") as fake_zip:
        fake_zip.writestr("bin/python.exe", "fake python payload")

    commands = []
    records = []

    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    core.log.addHandler(handler)
    log_level = core.log.level
    core.log.setLevel(logging.DEBUG)
    try:
        shell = core.Shell({}, "goodhost", user="admin", priv=fake_key, winrm=True)
        shell.send = lambda local, remote, makedirs=False: ("sent", "", 0)
        shell.exec_cmd = lambda cmd: commands.append(cmd) or ("DOTNET", "", 0)

        class FakeWrapper:
            pass

        wrapper = FakeWrapper()
        wrapper.shell = shell
        wrapper.python_env = str(python_env)
        core.set_winvars(wrapper)

        assert core.deploy_python(wrapper) is True
    finally:
        core.log.removeHandler(handler)
        core.log.setLevel(log_level)

    expand_cmd = [c for c in commands if "ExtractToFile" in c][0]
    assert "-Force" in expand_cmd  # fallback path keeps -Force semantics
    assert (
        "System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)"
        in expand_cmd
    )
    assert "bin.zip" in expand_cmd
    assert wrapper.python_dir in expand_cmd
    assert "Expand-Archive" in expand_cmd  # fallback present in the command
    timer_msgs = [m for m in records if "extracted" in m and "seconds" in m]
    assert timer_msgs, "expected a timing debug message"
    assert "System.IO.Compression" in timer_msgs[0]
    assert "seconds (retcode 0)" in timer_msgs[0]
    print("timer log:", timer_msgs[0])
