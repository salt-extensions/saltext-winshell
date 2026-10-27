# Managing Windows hosts with salt-ssh

The winshell extension lets salt-ssh manage Windows hosts **without
installing a minion**. Everything runs over OpenSSH: salt-ssh connects
to the target, uploads a tiny Python shim, and executes it with a
pre-staged Python environment that the extension deploys on first use.

:::{hint}
Despite the roster flag name, no WinRM is involved. The `winrm: True`
flag is a historical leftover from the original `saltwinshell` module;
it simply tells salt-ssh to treat the target as a Windows host. The
actual transport is plain SSH (paramiko) plus SCP for file transfers.
:::

## How it works

1. salt-ssh connects to the target over SSH using the salt-ssh private
   key (paramiko) and transfers files with SCP.
1. It runs `powershell $ENV:PROCESSOR_ARCHITECTURE` to determine the
   target architecture (`AMD64` or `x86`).
1. It uploads a per-command Python shim to `C:\saltremote\bin` and
   executes it with the deployed `python.exe`.
1. On first use the shim reports what is missing and salt-ssh
   automatically deploys:
   - **salt-thin** → `C:\saltremote\thin` (`salt-thin.tgz`)
   - **the Salt-Env Python environment** → `C:\saltremote\bin`
     (extracted from a `Salt-Env-<version>-<arch>-Py3.zip` archive)
1. Subsequent commands reuse the deployed environment until the shim
   signals that something (`deploy` or `ext_mods`) needs to be
   redeployed.

## Master-side prerequisites

The salt master (the machine running salt-ssh) needs three things:

### 1. The extension installed

Install the extension into the same Python environment Salt uses - see
[Installation](installation.md).

### 2. The Salt-Env archives

Place the Windows Python environment archives on the master at
`/extra/salt/saltwinshell/`:

```text
/extra/salt/saltwinshell/Salt-Env-3008-AMD64-Py3.zip
/extra/salt/saltwinshell/Salt-Env-3008-x86-Py3.zip
```

- The version in the filename must match `ENV_VERSION` from
  `saltext.winshell.env_version` (`3008` for this release).
- Keep **both** archives available unless every target is known to be
  `AMD64`; the architecture is detected at connect time and the wrong
  (or missing) archive causes a configuration error.
- These archives are intentionally not distributed with the extension
  \- you need to obtain them from your Salt environment (they are the
  `Salt-Env-<version>-AMD64/x86-Py3.zip` zips shipped alongside salt
  releases).

### 3. An SSH private key

- The default key is `/etc/salt/pki/master/ssh/salt-ssh.rsa`,
  auto-generated on first use.
- It can be overridden with the `ssh_priv` master option or with a
  per-target `priv` roster option.
- Only **key-based authentication** is used. The `passwd` roster
  option is ignored for Windows targets - make sure the target accepts
  the public key.

## Roster entries

Add each Windows host to your roster (by default `/etc/salt/roster`):

```yaml
win2012dev:
  host: win2012dev.example.com
  user: Administrator
  winrm: true
```

| Option  | Required | Description                                                                                               |
| ------- | -------- | --------------------------------------------------------------------------------------------------------- |
| `host`  | yes      | Hostname or IP address of the Windows target.                                                             |
| `user`  | yes      | The Windows account salt-ssh connects as.                                                                 |
| `winrm` | yes      | Must be `True` to enable Windows handling (see the note at the top).                                      |
| `priv`  | no       | Path to the SSH private key on the master. Falls back to `ssh_priv` and then to the default salt-ssh key. |

Multiple hosts can share the same settings:

```yaml
win2012dev:
  host: win2012dev.example.com
  user: Administrator
  winrm: true

win2019:
  host: win2019.example.com
  user: svc-salt
  priv: /etc/salt/pki/master/ssh/windows_key
  winrm: true
```

## Target host prerequisites

Each Windows host needs the following one-time setup.

### 1. OpenSSH Server

Enable and start the built-in Windows OpenSSH server:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic
Start-Service sshd
```

Also make sure the Windows firewall allows inbound TCP 22.

### 2. The master's public key

The key-based connection requires the master's public key (the `.pub`
file next to the salt-ssh private key, e.g.
`/etc/salt/pki/master/ssh/salt-ssh.rsa.pub`) in the target user's
`authorized_keys`:

- For regular users: `C:\Users\<user>\.ssh\authorized_keys`
- For accounts in the local Administrators group:
  `C:\ProgramData\ssh\administrators_authorized_keys` (as configured
  by the Windows OpenSSH default `sshd_config`)

Administrator key files need restrictive ACLs or sshd will refuse to
use them:

```powershell
icacls "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "SYSTEM:F" /grant "BUILTIN\Administrators:F"
```

### 3. PowerShell

PowerShell must be available - it is used for architecture detection
and for extracting the Python environment archive (via the .NET
`System.IO.Compression` API, with an `Expand-Archive` fallback on
older systems).

### 4. Account privileges

The connecting account must be able to create `C:\saltremote` and
write to it - typically a local Administrator account.

:::{hint}
No salt minion is required on the target - this is a fully agentless
setup.
:::

## Verifying the setup

Once the extension is installed on the master, the Salt-Env archives
are in place, and the target accepts the key:

```bash
salt-ssh 'win2012dev' test.version
salt-ssh 'win2012dev' cmd.run 'hostname'
salt-ssh 'win2012dev' winshell.version
```

`winshell.version` reports the installed extension version and the
version of the deployed Salt-Env environment. Expect the **first
command on a new host to be slow** - it deploys the thin and Python
environment archives. Later commands only transfer the small
per-command shim.

## Troubleshooting

- **Permission denied when connecting** - the public key is not being
  accepted. Check the `authorized_keys` location (administrator
  accounts must use `administrators_authorized_keys`) and the file
  ACLs.
- **`Python env ... doesn't exist`** - the master is missing the
  `Salt-Env-<version>-<arch>-Py3.zip` archive for the target's
  architecture, or it is not under `/extra/salt/saltwinshell/`.
- **First command hangs or times out** - deploying the archives can
  take a while on slow links; subsequent commands are much faster.
- **SCP send failures** - the extension retries each file transfer
  three times and drops the connection between attempts; a
  persistently failing transfer aborts with an error naming the file.
  A truncated file left behind by a failed transfer is never treated
  as success.
- For verbose logs run salt-ssh with `-l debug` (or `-l trace` to see
  the individual shim commands).
