"""
Salt execution module for the agentless Windows shell.

These functions run on the salt master alongside the salt-ssh client.
"""

import logging

import saltext.winshell.core
from saltext.winshell import __version__

log = logging.getLogger(__name__)

__virtualname__ = "winshell"


def __virtual__():
    # The core library needs paramiko/scp; surface a useful message when
    # they are missing instead of failing later at ssh time
    try:
        import paramiko  # noqa: F401
        import scp  # noqa: F401
    except ImportError as exc:
        return False, f"The winshell execution module cannot be loaded: {exc}"
    return __virtualname__


def version():
    """
    Return the winshell package and deployed Salt-Env versions

    CLI Example:

    .. code-block:: bash

        salt '*' winshell.version
    """
    return {"package": __version__, "env": saltext.winshell.core.version}
