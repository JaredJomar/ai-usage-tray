from pathlib import Path

import config


def test_get_ca_bundle_path_persists_certificate(monkeypatch, tmp_path):
    source = tmp_path / "source.pem"
    source.write_text("certificate-data", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "app")
    monkeypatch.setattr(config.certifi, "where", lambda: str(source))

    bundle_path = Path(config.get_ca_bundle_path())

    assert bundle_path == tmp_path / "app" / "certifi" / "cacert.pem"
    assert bundle_path.read_text(encoding="utf-8") == "certificate-data"


def test_get_ca_bundle_path_uses_existing_copy_when_source_is_missing(monkeypatch, tmp_path):
    bundle_path = tmp_path / "app" / "certifi" / "cacert.pem"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text("certificate-data", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "app")
    monkeypatch.setattr(config.certifi, "where", lambda: str(tmp_path / "missing.pem"))

    assert config.get_ca_bundle_path() == str(bundle_path)


def test_dark_mode_defaults_false_and_persists_through_config_file(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    assert config.load_config().dark_mode is False

    saved = config.AppConfig(dark_mode=True)
    config.save_config(saved)

    assert config.load_config().dark_mode is True


def test_legacy_config_without_dark_mode_keeps_default(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"refresh_interval_seconds": 120}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    loaded = config.load_config()

    assert loaded.refresh_interval_seconds == 120
    assert loaded.dark_mode is False


def test_set_dark_mode_preserves_settings_saved_after_dashboard_opens(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)
    config.save_config(config.AppConfig(refresh_interval_seconds=60, cache_ttl_seconds=30))

    dashboard_config = config.load_config()
    settings_config = config.load_config()
    settings_config.refresh_interval_seconds = 120
    settings_config.cache_ttl_seconds = 90
    config.save_config(settings_config)

    saved = config.set_dark_mode(not dashboard_config.dark_mode)

    assert saved.dark_mode is True
    assert saved.refresh_interval_seconds == 120
    assert saved.cache_ttl_seconds == 90
    assert config.load_config() == saved
