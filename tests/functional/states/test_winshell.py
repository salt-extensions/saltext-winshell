import pytest

pytestmark = [
    pytest.mark.requires_salt_states("winshell.exampled"),
]


@pytest.fixture
def winshell(states):
    return states.winshell


def test_replace_this_this_with_something_meaningful(winshell):
    echo_str = "Echoed!"
    ret = winshell.exampled(echo_str)
    assert ret.result
    assert not ret.changes
    assert echo_str in ret.comment
