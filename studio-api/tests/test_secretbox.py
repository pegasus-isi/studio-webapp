"""At-rest secret encryption (S7)."""

from __future__ import annotations

import asyncio

import secretbox


def test_roundtrip_with_secret(monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", "unit-test-master-secret")
    assert secretbox.is_active() is True

    token = secretbox.encrypt("sk-super-secret")
    assert token.startswith("enc:v1:")
    assert "sk-super-secret" not in token  # ciphertext, not plaintext
    assert secretbox.decrypt(token) == "sk-super-secret"


def test_encrypt_is_idempotent(monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", "s")
    once = secretbox.encrypt("k")
    assert secretbox.encrypt(once) == once  # already tagged — untouched


def test_plaintext_fallback_without_secret(monkeypatch):
    monkeypatch.delenv("STUDIO_SECRET_KEY", raising=False)
    monkeypatch.delenv("STUDIO_SECRET_KEY_FILE", raising=False)
    assert secretbox.is_active() is False
    # Fail-open: stored as plaintext, read back unchanged.
    assert secretbox.encrypt("sk-plain") == "sk-plain"
    assert secretbox.decrypt("sk-plain") == "sk-plain"


def test_legacy_plaintext_reads_through_even_with_secret(monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", "s")
    # A pre-encryption value (no enc:v1: tag) must still read back verbatim so
    # existing rows keep working and migrate on next write.
    assert secretbox.decrypt("sk-legacy-plaintext") == "sk-legacy-plaintext"


def test_empty_and_none_passthrough(monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", "s")
    assert secretbox.encrypt("") == ""
    assert secretbox.encrypt(None) is None
    assert secretbox.decrypt("") == ""
    assert secretbox.decrypt(None) is None


def test_undecryptable_returns_empty_not_ciphertext(monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", "key-A")
    token = secretbox.encrypt("sk-x")
    # Rotate/lose the key: the tagged value can't be decrypted -> "" (never the
    # raw ciphertext leaking out).
    monkeypatch.setenv("STUDIO_SECRET_KEY", "key-B")
    assert secretbox.decrypt(token) == ""


def test_secret_key_file(monkeypatch, tmp_path):
    monkeypatch.delenv("STUDIO_SECRET_KEY", raising=False)
    keyfile = tmp_path / "secret"
    keyfile.write_text("from-a-file\n")
    monkeypatch.setenv("STUDIO_SECRET_KEY_FILE", str(keyfile))
    token = secretbox.encrypt("sk-file")
    assert token.startswith("enc:v1:")
    assert secretbox.decrypt(token) == "sk-file"


# --- DB integration: the api_key column is stored encrypted ---


def _row_and_config(tmp_path, monkeypatch):
    from db import Database

    async def go():
        db = Database(str(tmp_path / "s7.db"))
        await db.connect()
        await db.set_llm_config(provider="anthropic", api_key="sk-at-rest")
        async with db.db.execute(
            "SELECT api_key FROM llm_config WHERE id = 1"
        ) as cur:
            raw = (await cur.fetchone())["api_key"]
        cfg = await db.get_llm_config()

        await db.upsert_provider_config(provider_id="p1", name="P", api_key="sk-p")
        async with db.db.execute(
            "SELECT api_key FROM provider_configs WHERE provider_id = 'p1'"
        ) as cur:
            raw_p = (await cur.fetchone())["api_key"]
        got_p = await db.get_provider_config("p1")
        await db.close()
        return raw, cfg["api_key"], raw_p, got_p["api_key"]

    return asyncio.run(go())


def test_db_stores_keys_encrypted_and_reads_them_back(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_KEY", "db-master-secret")
    raw, cfg_key, raw_p, prov_key = _row_and_config(tmp_path, monkeypatch)

    # On disk: ciphertext, tagged, not the plaintext.
    assert raw.startswith("enc:v1:") and "sk-at-rest" not in raw
    assert raw_p.startswith("enc:v1:") and "sk-p" not in raw_p
    # Through the API layer: plaintext, decrypted.
    assert cfg_key == "sk-at-rest"
    assert prov_key == "sk-p"


def test_db_plaintext_when_no_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_SECRET_KEY", raising=False)
    monkeypatch.delenv("STUDIO_SECRET_KEY_FILE", raising=False)
    raw, cfg_key, _raw_p, _prov_key = _row_and_config(tmp_path, monkeypatch)
    # Fail-open: unencrypted on disk, still readable.
    assert raw == "sk-at-rest"
    assert cfg_key == "sk-at-rest"
