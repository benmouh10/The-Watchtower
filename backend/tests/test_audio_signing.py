"""
Tests for signed playback URLs.

These tokens are the only reason a media element can load a narration at all: the
routes stay authenticated, and the signature is what carries the listener's
identity to the stream. That makes the signature's scope the whole security story,
so these tests pin what a token may and may not do: it must open exactly one
resource, for exactly the account it was minted for, and only until it expires.
"""
import time
import uuid

import pytest

from app.services import audio_signing


LISTENER = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER = uuid.UUID("99999999-8888-7777-6666-555555555555")


class TestIssueAndVerify:
    def test_a_fresh_token_carries_its_listener_and_role(self):
        minted = audio_signing.issue("article:" + str(LISTENER), LISTENER, "ADMIN")

        verified = audio_signing.verify("article:" + str(LISTENER), minted["token"])

        assert verified == {"user_id": LISTENER, "role": "ADMIN"}

    def test_expiry_is_about_ten_minutes_out(self):
        before = int(time.time())
        minted = audio_signing.issue("article:" + str(LISTENER), LISTENER, "AUTH_USER")

        assert before + 595 <= minted["expires_at"] <= before + 605

    def test_a_token_cannot_open_a_different_resource(self):
        minted = audio_signing.issue("article:" + str(LISTENER), LISTENER, "AUTH_USER")

        # Same listener, same role: still refused, because the resource is signed.
        assert audio_signing.verify("article:" + str(OTHER), minted["token"]) is None

    def test_a_token_for_one_article_does_not_open_another(self):
        first = audio_signing.issue(f"article:{uuid.uuid4()}", LISTENER, "AUTH_USER")
        second_id = uuid.uuid4()

        assert audio_signing.verify(f"article:{second_id}", first["token"]) is None

    def test_an_event_token_does_not_open_an_article(self):
        minted = audio_signing.issue(f"event:{LISTENER}", LISTENER, "AUTH_USER")

        assert audio_signing.verify(f"article:{LISTENER}", minted["token"]) is None

    def test_an_expired_token_is_refused(self):
        # issue() floors the TTL at 30s, so mint one directly into the past.
        payload = f"article:{LISTENER}|{LISTENER}|AUTH_USER|{int(time.time()) - 1}"
        token = "%s.%s" % (
            audio_signing._b64(payload.encode()),
            audio_signing._b64(audio_signing._mac(payload)),
        )

        assert audio_signing.verify("article:" + str(LISTENER), token) is None

    def test_a_token_signed_with_another_key_is_refused(self, monkeypatch):
        minted = audio_signing.issue("article:" + str(LISTENER), LISTENER, "AUTH_USER")

        monkeypatch.setattr(audio_signing, "_signing_key", lambda: b"a different key")

        assert audio_signing.verify("article:" + str(LISTENER), minted["token"]) is None

    def test_a_tampered_payload_is_refused(self):
        minted = audio_signing.issue("article:" + str(LISTENER), LISTENER, "AUTH_USER")
        body, signature = minted["token"].split(".", 1)
        forged = "%s.%s" % (audio_signing._b64(b"article:x|someone|ADMIN|99999999999"), signature)

        assert audio_signing.verify("article:" + str(LISTENER), forged) is None

    @pytest.mark.parametrize(
        "token",
        ["", "no-dot", "...", "!!!.###", "YQ", "YQ.", "a.b.c.d.e"],
    )
    def test_malformed_tokens_are_refused_without_raising(self, token):
        assert audio_signing.verify("article:" + str(LISTENER), token) is None

    def test_two_listeners_get_different_tokens_for_the_same_resource(self):
        first = audio_signing.issue("article:" + str(LISTENER), LISTENER, "AUTH_USER")
        second = audio_signing.issue("article:" + str(LISTENER), OTHER, "ADMIN")

        assert first["token"] != second["token"]
        assert audio_signing.verify("article:" + str(LISTENER), first["token"])["user_id"] == LISTENER
        assert audio_signing.verify("article:" + str(LISTENER), second["token"])["user_id"] == OTHER

    def test_the_identity_in_the_token_is_what_a_stream_uses(self):
        """The route trusts the signed identity, so it must be tamper-evident."""
        minted = audio_signing.issue("article:" + str(LISTENER), OTHER, "ADMIN")

        verified = audio_signing.verify("article:" + str(LISTENER), minted["token"])

        assert verified["user_id"] == OTHER, "token states the account it was minted for"
        assert verified["role"] == "ADMIN"