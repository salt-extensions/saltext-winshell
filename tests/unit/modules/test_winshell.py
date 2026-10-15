import saltext.winshell.modules.winshell_mod as winshell_module


def test_version_reports_package_and_env():
    ret = winshell_module.version()
    assert set(ret) == {"package", "env"}
    assert ret["env"] == "3008"
    assert isinstance(ret["package"], str)
