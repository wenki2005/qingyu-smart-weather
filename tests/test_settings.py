"""测试运行时设置（数据源开关）的校验与持久化。"""
import json

import pytest

from weather_desktop import DEFAULT_RUNTIME_SETTINGS, DesktopAPI


@pytest.fixture
def api(tmp_path, monkeypatch):
    """构造 DesktopAPI，并把设置文件重定向到临时目录（隔离真实用户设置）。"""
    from app.config import Settings
    # 在构造前就替换路径：构造时会读取 settings.json，若指向项目根目录会
    # 读到用户实际运行留下的 weather_desktop_settings.json，导致断言不稳定。
    monkeypatch.setattr(DesktopAPI, "_settings_path", lambda self: str(tmp_path / "settings.json"))
    return DesktopAPI(Settings(city="北京"))

def test_default_settings(api):
    s = api.get_settings()
    assert s["show_official"] is True
    assert s["nmc_warnings"] is True
    assert s["open_meteo_models"] == ""


def test_set_setting_validates_model(api):
    r = api.set_setting("open_meteo_models", "cma_grapes_global")
    assert r["ok"] is True
    assert api.get_settings()["open_meteo_models"] == "cma_grapes_global"
    # 非法值回退为空
    api.set_setting("open_meteo_models", "hacker_model")
    assert api.get_settings()["open_meteo_models"] == ""


def test_set_setting_booleans(api):
    api.set_setting("show_official", False)
    api.set_setting("nmc_warnings", False)
    api.set_setting("typhoon_warnings", False)
    s = api.get_settings()
    assert s["show_official"] is False
    assert s["nmc_warnings"] is False
    assert s["typhoon_warnings"] is False


def test_set_setting_unknown_key(api):
    r = api.set_setting("nonexistent", True)
    assert r["ok"] is False


def test_settings_persist_to_file(api):
    api.set_setting("show_official", False)
    api.set_setting("open_meteo_models", "cma_grapes_global")
    with open(api._settings_path(), encoding="utf-8") as f:
        data = json.load(f)
    assert data["show_official"] is False
    assert data["open_meteo_models"] == "cma_grapes_global"


def test_defaults_cover_all_keys():
    assert set(DEFAULT_RUNTIME_SETTINGS) == {
        "open_meteo_models", "show_official", "nmc_warnings", "typhoon_warnings",
    }
