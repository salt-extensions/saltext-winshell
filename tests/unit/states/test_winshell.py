import saltext.winshell.states.winshell_mod as winshell_state


def test_virtual_reports_no_states():
    ret = winshell_state.__virtual__()
    assert ret[0] is False
    assert "does not provide any states" in ret[1]
