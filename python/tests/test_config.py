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


def test_launch_on_startup_defaults_true_and_explicit_false_is_preserved(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    assert config.load_config().launch_on_startup is True

    config_path.write_text('{"launch_on_startup": false}', encoding="utf-8")
    assert config.load_config().launch_on_startup is False


def test_legacy_config_without_dark_mode_keeps_default(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"refresh_interval_seconds": 120}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    loaded = config.load_config()

    assert loaded.refresh_interval_seconds == 120
    assert loaded.dark_mode is False
    assert loaded.section_visibility == config.SectionVisibility()


def test_legacy_reset_alert_settings_keep_existing_values_and_add_new_defaults(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"reset_credit_notify_60_minutes": 45, "reset_credit_notify_15_minutes": 10}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    loaded = config.load_config()

    assert loaded.reset_credit_notify_60_minutes == 45
    assert loaded.reset_credit_notify_30_minutes == 30
    assert loaded.reset_credit_notify_15_minutes == 10
    assert loaded.reset_credit_notify_5_minutes == 5


def test_section_visibility_defaults_all_visible_and_round_trips(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    assert config.load_config().section_visibility == config.SectionVisibility(
        balance=True, credits=True, usage_breakdown=True, local_activity=True,
    )

    saved = config.AppConfig()
    saved.section_visibility.usage_breakdown = False
    config.save_config(saved)

    reloaded = config.load_config()
    assert reloaded.section_visibility.usage_breakdown is False
    assert reloaded.section_visibility.balance is True


def test_partial_or_invalid_section_visibility_is_coerced_safely(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"section_visibility": {"balance": false, "credits": "nope"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    partial = config.load_config().section_visibility
    assert partial.balance is False
    assert partial.credits is True
    assert partial.usage_breakdown is True
    assert partial.local_activity is True

    config_path.write_text('{"section_visibility": "broken"}', encoding="utf-8")
    broken = config.load_config().section_visibility
    assert broken == config.SectionVisibility()


def test_set_section_visibility_preserves_settings_saved_after_dashboard_opens(monkeypatch, tmp_path):
    config_path = tmp_path / "app" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_path.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)
    config.save_config(config.AppConfig(refresh_interval_seconds=60, cache_ttl_seconds=30))

    dashboard_config = config.load_config()
    settings_config = config.load_config()
    settings_config.refresh_interval_seconds = 120
    settings_config.cache_ttl_seconds = 90
    config.save_config(settings_config)

    visibility = config.SectionVisibility(local_activity=False)
    saved = config.set_section_visibility(visibility)

    assert saved.section_visibility.local_activity is False
    assert saved.refresh_interval_seconds == 120
    assert saved.cache_ttl_seconds == 90
    assert saved.dark_mode == dashboard_config.dark_mode
    assert config.load_config() == saved


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
