import pytest

pytestmark = [
    pytest.mark.requires_salt_modules("winshell.example_function"),
]


@pytest.fixture
def winshell(modules):
    return modules.winshell


def test_replace_this_this_with_something_meaningful(winshell):
    echo_str = "Echoed!"
    res = winshell.example_function(echo_str)
    assert res == echo_str
