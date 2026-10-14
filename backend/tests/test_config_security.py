"""
Hermetic tests for secret handling and production config hardening.

These never touch a database, cache or network: they pin the guarantees that a
production process refuses to boot with a weak SECRET_KEY and that secrets are
masked in repr output and in the redaction helpers used by logging.
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.redaction import MASK, mask_url_credentials, redact_mapping


class TestProductionSecretValidation:
    def test_rejects_default_secret_key(self):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                APP_ENV="production",
                SECRET_KEY="CHANGE_ME_IN_PRODUCTION",
            )

    def test_rejects_short_secret_key(self):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, APP_ENV="production", SECRET_KEY="tooshort")

    def test_accepts_strong_secret_key(self):
        key = "k" * 48
        cfg = Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY=key,
            SMTP_HOST="smtp.gmail.com",
        )
        assert cfg.SECRET_KEY == key

    def test_development_tolerates_default_secret_key(self):
        # SECRET_KEY is passed explicitly on purpose: _env_file=None stops
        # pydantic-settings reading .env, but it still reads the process
        # environment, and the compose dev stack exports SECRET_KEY. Without this
        # the assertion depends on where the suite happens to run.
        cfg = Settings(
            _env_file=None,
            APP_ENV="development",
            SECRET_KEY="CHANGE_ME_IN_PRODUCTION",
        )
        assert cfg.SECRET_KEY == "CHANGE_ME_IN_PRODUCTION"


class TestProductionSmtpValidation:
    """A production boot must not silently use the development Mailpit sink."""

    _KEY = "k" * 48

    @pytest.mark.parametrize("sink", ["", "mailpit", "localhost", "127.0.0.1"])
    def test_rejects_sink_host_in_production(self, sink):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                APP_ENV="production",
                SECRET_KEY=self._KEY,
                SMTP_HOST=sink,
            )

    def test_accepts_real_host_in_production(self):
        cfg = Settings(
            _env_file=None,
            APP_ENV="production",
            SECRET_KEY=self._KEY,
            SMTP_HOST="smtp.gmail.com",
            SMTP_PORT=587,
            SMTP_STARTTLS=True,
        )
        assert cfg.SMTP_HOST == "smtp.gmail.com"

    def test_development_allows_mailpit(self):
        cfg = Settings(_env_file=None, APP_ENV="development", SMTP_HOST="mailpit")
        assert cfg.SMTP_HOST == "mailpit"


class TestRedaction:
    def test_url_password_is_masked(self):
        masked = mask_url_credentials(
            "postgresql+asyncpg://globelens:s3cr3t@db:5432/globelens_db"
        )
        assert "s3cr3t" not in masked
        assert MASK in masked

    def test_sensitive_keys_masked_but_unset_stays_empty(self):
        out = redact_mapping(
            {"SECRET_KEY": "abc", "SMTP_PASSWORD": "", "SMTP_HOST": "mailpit"}
        )
        assert out["SECRET_KEY"] == MASK
        assert out["SMTP_PASSWORD"] == ""
        assert out["SMTP_HOST"] == "mailpit"

    def test_nested_values_are_redacted(self):
        out = redact_mapping({"creds": {"api_key": "sk-123", "host": "x"}})
        assert out["creds"]["api_key"] == MASK
        assert out["creds"]["host"] == "x"

    def test_settings_repr_hides_secrets(self):
        cfg = Settings(
            _env_file=None,
            SECRET_KEY="super-secret-value-1234567890123456",
            OPENAI_API_KEY="sk-live-abcdef",
        )
        text = repr(cfg)
        assert "super-secret-value" not in text
        assert "sk-live-abcdef" not in text
        assert MASK in text

    def test_module_settings_redacted_exposes_no_secret(self):
        redacted = settings.redacted()
        assert redacted["SECRET_KEY"] != settings.SECRET_KEY or settings.SECRET_KEY == ""
        assert "globelens_secret" not in str(redacted)
