"""
Core functions to impliment the salt windows shell
"""

# Copyright 2021 VMware, Inc.
# SPDX-License-Identifier: Apache-2.0

import logging
import ntpath
import os
import time

import paramiko
import salt.exceptions
import scp
from salt.client.ssh.shell import Shell as LinuxShell

from saltext.winshell.env_version import ENV_VERSION

log = logging.getLogger(__name__)

# Windows Powershell Shim - actually, this is python
SSH_PS_SHIM = "\n".join([s.strip() for s in r'''
import base64

exec(base64.b64decode("""{SSH_PY_CODE}"""))
'''.split("\n")])


def set_winvars(self, ssh_python_env=None):  # pylint: disable=unused-argument
    """
    Set the Win Vars
    """
    self.thin_dir = "c:/saltremote/thin"
    self.python_dir = "c:/saltremote/bin"
    pyver = "Py3"
    self.python_env_map = {
        "AMD64": f"Salt-Env-{ENV_VERSION}-AMD64-{pyver}.zip",
        "x86": f"Salt-Env-{ENV_VERSION}-x86-{pyver}.zip",
    }
    self.python_saltwinshell = "/extra/salt/saltwinshell"


def gen_shim(py_code_enc):
    """
    Generate a PowerShell shim
    """
    return SSH_PS_SHIM.format(SSH_PY_CODE=py_code_enc)


def get_target_shim_file(self, target_shim_file):
    """
    Get the target shim file
    """
    return ntpath.normpath(ntpath.sep.join((self.python_dir, target_shim_file)))


def call_python(self, target_shim_file):
    """
    Call python stuff
    """
    python_exe = ntpath.normpath(ntpath.sep.join((self.python_dir, "python.exe")))
    return self.shell.exec_cmd(f"{python_exe} {ntpath.normpath(target_shim_file)}")


def deploy_python(self):
    """
    Deploy the Windows python environment
    """
    if not self.python_env:
        log.debug("No Python Environment found. Determining which env to use")
        self.python_env = os.path.join(self.python_saltwinshell, self.python_env_map[self.arch])
        if not os.path.isfile(self.python_env):
            if os.path.isfile(
                os.path.join(self.python_saltwinshell, self.python_env_map[self.arch])
            ):
                self.python_env = os.path.join(
                    self.python_saltwinshell, self.python_env_map[self.arch]
                )
        if not os.path.isfile(self.python_env):
            raise salt.exceptions.SaltConfigurationError(
                f"Python env: {self.python_env} doesn't exist."
            )
    self.shell.send(
        self.python_env,
        os.path.join(self.python_dir, "bin.zip"),
        makedirs=True,
    )

    zip_path = os.path.join(self.python_dir, "bin.zip")
    # Extract via .NET System.IO.Compression first (much faster than
    # Expand-Archive); PS 5.1/.NET Framework has no overwrite flag on
    # ZipFile.ExtractToDirectory, so extract entry-by-entry with
    # ZipFileExtensions.ExtractToFile($entry, $target, $true). On any error
    # fall back to Expand-Archive -Force
    extract_cmd = (
        f"powershell \"$ErrorActionPreference='Stop'; "
        f"try {{ "
        f"Add-Type -AssemblyName System.IO.Compression.FileSystem; "
        f"$zip = [System.IO.Compression.ZipFile]::OpenRead('{zip_path}'); "
        f"foreach ($entry in $zip.Entries) {{ "
        f"if ($entry.Name -eq '') {{ continue }}; "
        f"$target = Join-Path '{self.python_dir}' $entry.FullName; "
        f"$dir = Split-Path $target -Parent; "
        f"if (-not (Test-Path $dir)) {{ New-Item -ItemType Directory -Path $dir -Force | Out-Null }}; "
        f"[System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true) }}; "
        f"$zip.Dispose(); "
        f"Write-Output 'DOTNET' "
        f"}} catch {{ "
        f"Write-Output ('DOTNET FAILED: ' + $_.Exception.Message); "
        f"Expand-Archive -Path '{zip_path}' -DestinationPath '{self.python_dir}' -Force; "
        f"Write-Output 'EXPAND-ARCHIVE' }}"
    )
    start = time.time()
    stdout, stderr, retcode = self.shell.exec_cmd(extract_cmd)
    elapsed = time.time() - start
    if "DOTNET FAILED" not in stdout and "DOTNET" in stdout:
        method = "System.IO.Compression"
    elif "EXPAND-ARCHIVE" in stdout:
        method = "Expand-Archive fallback"
        log.debug("dotnet extraction failed: %s", stdout.strip())
    else:
        method = "unknown"
        log.error("extraction failed: stdout=%s stderr=%s retcode=%s", stdout, stderr, retcode)
    log.debug(
        "extracted %s via %s in %.2f seconds (retcode %s)", zip_path, method, elapsed, retcode
    )
    log.trace("shim_cmd %s - %s - %s", extract_cmd, stdout, retcode)
    return True


class Shell(LinuxShell):
    send_retries = 3
    retry_delay = 5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._conn = None

    def get_connection(self):
        """
        Return a cached Connection, creating it if necessary.
        Returns None if the connection cannot be established.
        """
        conn = self._conn
        if conn is not None and conn.connected:
            return conn
        if conn is not None:
            conn.close()
            self._conn = None
        conn = Connection(self.host, self.user, self.priv)
        if conn.connect():
            self._conn = conn
            return conn
        return None

    def drop_connection(self):
        """
        Close and forget the cached connection
        """
        conn = self._conn
        if conn is not None:
            conn.close()
        self._conn = None

    def send(self, local, remote, makedirs=False):
        """
        Send a file to the remote system using scp, retrying on failure.

        Raises SaltException once all retries are exhausted - a failed
        scp leaves a truncated file on the target which the shim will
        happily unpack, so a failed send must never look like success.
        """
        last_err = "no attempt made"
        for attempt in range(1, self.send_retries + 1):
            conn = self.get_connection()
            if conn is None:
                last_err = f"Unable to connect to {self.host}"
                log.error(
                    "send attempt %s/%s to %s failed: %s",
                    attempt,
                    self.send_retries,
                    self.host,
                    last_err,
                )
                time.sleep(self.retry_delay)
                continue

            if makedirs:
                log.debug(
                    "Making directory %s on %s", ntpath.dirname(ntpath.normpath(remote)), self.host
                )
                ret = conn.cmd_exec(f"mkdir {ntpath.dirname(ntpath.normpath(remote))}")
                log.trace("%s - %s - %s", ret[0], ret[1], ret[2])

            log.debug(
                "Copying %s to %s on minion (attempt %s/%s)",
                local,
                remote,
                attempt,
                self.send_retries,
            )
            ret = conn.send(local, remote)

            if ret and ret[2] == 0:
                log.trace("COPY %s - %s - %s", ret[0], ret[1], ret[2])
                return ret[0], ret[1], ret[2]

            last_err = str(ret[1]) if ret else "no result from scp"
            log.error(
                "send attempt %s/%s failed for %s -> %s on %s: %s",
                attempt,
                self.send_retries,
                local,
                remote,
                self.host,
                last_err,
            )
            # a failed scp can leave a truncated file on the target - drop
            # the cached connection so the retry starts clean
            self.drop_connection()
            time.sleep(self.retry_delay)

        raise salt.exceptions.SaltException(
            f"Failed to send {local} to {remote} on {self.host} after {self.send_retries} attempts: {last_err}"
        )

    def exec_cmd(self, cmd):
        """
        Execute a remote command
        """
        logmsg = f"Executing command: {cmd}"
        if 'decode("base64")' in logmsg or "base64.b64decode(" in logmsg:
            log.debug("Executed SHIM command. Command logged to TRACE")
            log.trace(logmsg)
        else:
            log.debug(logmsg)

        conn = self.get_connection()
        if conn is None:
            return "", f"Unable to connect to {self.host}", 1
        ret, err, code = conn.cmd_exec(cmd)
        log.trace("COMMAND: cmd:%s - ret:%s - err:%s - code:%s", cmd, ret, err, code)
        return ret, err, code


class Connection:
    def __init__(self, host, user, key="/etc/salt/pki/master/ssh/salt-ssh.id_ed25519"):
        self.host = host
        self.user = user
        self.key = key
        self.conn = paramiko.client.SSHClient()
        self.conn.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.connected = False
        if not self.key or not os.path.isfile(self.key):
            raise salt.exceptions.SaltConfigurationError(
                f"ssh config error: {self.key} doesn't exist."
            )
        if not os.access(self.key, os.R_OK):
            raise salt.exceptions.SaltConfigurationError(f"cannot access: {self.key}")

    def connect(self, host=None, user=None):
        if host:
            self.host = host
        if user:
            self.user = user
        try:
            self.conn.connect(self.host, username=self.user, key_filename=self.key)
            self.connected = True
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.error("Unable to connect to %s as %s: %s", self.host, self.user, err)
            self.connected = False

        return self.connected

    def connect_scp(self):
        scp_client = None
        if not self.connected:
            self.connect()

        if self.connected:
            scp_client = scp.SCPClient(self.conn.get_transport())

        return scp_client

    def close(self):
        """
        Close the ssh connection
        """
        try:
            self.conn.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        self.connected = False

    def send(self, local, remote):
        remote_path = remote.replace("c:", "").replace("\\", "/")
        err = None
        stdout = None
        code = None

        try:
            scp_c = self.connect_scp()
            if scp_c:
                scp_c.put(local, remote_path)
                scp_c.close()
                code = 0
            else:
                stdout = "Failed to open SCP"
                err = "SCP connection failed"
                code = 1
        except Exception as e:  # pylint: disable=broad-exception-caught
            stdout = f"Failed to upload file {remote_path}"
            err = e
            code = 1

        return stdout, err, code

    def cmd_exec(self, cmd):
        ret = None
        err = None
        code = None

        if not self.connected:
            self.connect()

        if self.connected:
            try:
                _, stdout, stderr = self.conn.exec_command(cmd)
                ret = stdout.read().decode()
                err = stderr.read().decode()
                code = 0
                # The shim signals the master by printing a bare command
                # ('deploy' or 'ext_mods') as the first line of stdout.
                # Match that exactly - a substring match anywhere in the
                # output would trigger spurious redeploys whenever a
                # command result happens to contain those words.
                first_line = ret.split("\n", 1)[0].strip()
                if first_line == "deploy":
                    code = 11
                elif first_line == "ext_mods":
                    code = 13
                elif "exists but is not a directory" in err:
                    code = 73
                if err and "WARNING" not in err:
                    code = 1
            except Exception as exc:  # pylint: disable=broad-exception-caught
                log.error("Command execution failed on %s: %s", self.host, exc)
                # the connection is likely dead - force a reconnect on the
                # next call
                self.connected = False
                ret = ""
                err = f"Command execution failed: {exc}"
                code = 1

        else:
            ret = ""
            err = f"Unable to connect to {self.host}"
            code = 1

        return ret, err, code
