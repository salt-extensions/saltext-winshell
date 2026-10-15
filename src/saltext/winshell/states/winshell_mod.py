"""
Salt state module for the agentless Windows shell.

No states are implemented yet - the winshell execution module and the
salt-ssh shell integration are the supported interfaces.
"""

import logging

log = logging.getLogger(__name__)

__virtualname__ = "winshell"


def __virtual__():
    return False, "The winshell state module does not provide any states yet"
