"""测试运行时设置（数据源开关）的校验与持久化。"""
import json

import pytest

from weather_desktop import DEFAULT_RUNTIME_SETTINGS, DesktopAPI


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app.config import Settings
    obj = DesktopAPI(Settings(city="北京"))
    monkeypatch.setattr(obj, "_settings_path", lambda: str(tmp_path / "settings.json"))
    return obj


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
