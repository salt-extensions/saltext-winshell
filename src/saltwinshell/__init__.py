# -*- coding: utf-8 -*-
'''
Compatibility shim for Salt's agentless Windows support.

Salt (3006/3008) imports this module by its original name whenever a
roster entry carries ``winrm: True``::

    salt/client/ssh/shell.py:   import saltwinshell
    salt/client/ssh/__init__.py:    import saltwinshell

The implementation now lives in the :mod:`winshell` package; this shim
re-exports the interface Salt calls so existing masters keep working
without patching Salt.
'''
# Copyright 2021 VMware, Inc.
# SPDX-License-Identifier: Apache-2.0

from saltext.winshell.core import (  # noqa: F401
    SSH_PS_SHIM,
    Shell,
    Connection,
    call_python,
    deploy_python,
    gen_shim,
    get_target_shim_file,
    set_winvars,
)
from saltext.winshell.env_version import version  # noqa: F401

__all__ = [
    "SSH_PS_SHIM",
    "Shell",
    "Connection",
    "call_python",
    "deploy_python",
    "gen_shim",
    "get_target_shim_file",
    "set_winvars",
    "version",
]
