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
