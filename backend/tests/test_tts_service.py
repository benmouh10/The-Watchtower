"""
Hermetic tests for the narration pipeline.

No speech provider is ever contacted and no model is ever loaded: synthesis is
replaced by a stub, or by a fake Kokoro engine, so these tests pin what matters
for correctness and cost — how article text is flattened and truncated, how it is
split to respect the provider's per-request character ceiling, that joined MP3
chunks do not repeat an ID3 header, that the local engine's WAV chunks are joined
by frame rather than by header, that the response advertises the container it
actually contains, and that a second play button serves the cache instead of
paying for the audio again.
"""
import asyncio
import base64
import io
import wave

import pytest

from app.services import tts_service as tts_module
from app.services.tts_service import (
    TTSService,
    TTSUnavailable,
    audio_extension,
    encode_mp3,
    join_audio,
    join_wav,
    sniff_content_type,
    split_for_speech,
    stream_chunks,
    strip_id3,
    to_speech_text,
    wav_frames,
)


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.writes = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.writes.append((key, ex))
        return True


@pytest.fixture
def fake_cache(monkeypatch):
    fake = _FakeRedis()

    async def _get_client():
        return fake

    monkeypatch.setattr(tts_module.cache_service, "_get_client", _get_client)
    return fake


@pytest.fixture
def service(monkeypatch):
    """A TTSService that believes it is configured, with no provider client."""
    monkeypatch.setattr(TTSService, "configured", property(lambda self: True))
    return TTSService()


@pytest.fixture
def local_provider(monkeypatch):
    """A TTSService pinned to the local provider, for the streaming tests."""
    monkeypatch.setattr(TTSService, "configured", property(lambda self: True))
    svc = TTSService()
    monkeypatch.setattr(svc, "_provider", "local")
    monkeypatch.setattr(tts_module.settings, "TTS_LOCAL_VOICE", "af_heart")
    monkeypatch.setattr(tts_module, "_mp3_available", lambda: False)
    return svc


def _tagged(frames: bytes, tag_body: bytes = b"") -> bytes:
    """Wrap audio frames in an ID3v2 tag, the way providers return a chunk.

    The 4 size bytes in an ID3v2 header count the tag body only — the 10-byte
    header itself, and the MP3 frames after it, are excluded.
    """
    size = len(tag_body)
    synchsafe = bytes(
        [
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ]
    )
    return b"ID3\x03\x00\x00" + synchsafe + tag_body + frames


def _wav(frames: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    """Build a RIFF/WAVE chunk the way the local engine hands audio back."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return buffer.getvalue()


class TestToSpeechText:
    def test_plain_text_passes_through_unchanged(self):
        assert to_speech_text("A short dispatch.", 500) == "A short dispatch."

    def test_newsroom_markup_is_flattened_to_spoken_words(self):
        html = "<p>Council <b>approved</b> the budget.</p><p>Second&nbsp;paragraph.</p>"
        assert to_speech_text(html, 500) == (
            "Council approved the budget. Second paragraph."
        )

    def test_script_and_style_content_is_never_spoken(self):
        html = "<p>Report.</p><script>alert('x')</script><style>p{color:red}</style>"
        assert to_speech_text(html, 500) == "Report."

    def test_mathematical_angles_are_not_mistaken_for_markup(self):
        # A naive tag stripper would silently delete "a < b and c > d".
        assert to_speech_text("Pressure fell when a < b and c > d.", 500) == (
            "Pressure fell when a < b and c > d."
        )

    def test_whitespace_and_blank_lines_are_normalised(self):
        assert to_speech_text("One   two\n\n\n\nThree\n", 500) == "One two\n\nThree"

    def test_truncation_prefers_a_sentence_boundary(self):
        text = "First sentence. Second sentence. Third sentence."
        result = to_speech_text(text, 25)
        assert result == "First sentence."

    def test_truncation_falls_back_to_a_hard_cut_without_a_sentence(self):
        result = to_speech_text("alpha beta gamma delta", 12)
        assert len(result) <= 12
        assert result.startswith("alpha")

    def test_empty_input_is_empty_output(self):
        assert to_speech_text("", 500) == ""
        assert to_speech_text(None, 500) == ""


class TestSplitForSpeech:
    def test_short_text_stays_in_one_chunk(self):
        assert split_for_speech("one two three", 100) == ["one two three"]

    def test_paragraphs_are_kept_apart(self):
        assert split_for_speech("First para.\n\nSecond para.", 100) == [
            "First para.",
            "Second para.",
        ]

    def test_no_chunk_exceeds_the_limit(self):
        text = ". ".join(f"Sentence number {i} carries a little weight" for i in range(40))
        for chunk in split_for_speech(text, 120):
            assert len(chunk) <= 120

    def test_splitting_preserves_every_word(self):
        text = " ".join(f"word{i}" for i in range(200))
        joined = " ".join(split_for_speech(text, 50))
        assert joined.split() == text.split()

    def test_a_sentence_longer_than_the_limit_is_split_on_words(self):
        monster = " ".join(f"filler{i}" for i in range(60))
        chunks = split_for_speech(monster, 50)
        assert len(chunks) > 1
        assert all(len(chunk) <= 50 for chunk in chunks)
        assert " ".join(chunks).split() == monster.split()

    def test_blank_paragraphs_are_dropped(self):
        assert split_for_speech("Only this.\n\n\n", 100) == ["Only this."]

    def test_non_positive_limit_is_rejected(self):
        with pytest.raises(ValueError):
            split_for_speech("text", 0)


class TestStripId3:
    def test_id3v2_header_is_removed(self):
        assert strip_id3(_tagged(b"FRAMES", b"\x00\x01\x02")) == b"FRAMES"

    def test_raw_frames_are_left_alone(self):
        assert strip_id3(b"\xff\xfb\x90\x00data") == b"\xff\xfb\x90\x00data"

    def test_size_is_read_as_synchsafe_not_byte_wise(self):
        # 128 bytes encodes as 00 00 01 00. Reading those four bytes as a plain
        # integer would give 256 and swallow the audio frames behind the tag.
        tagged = _tagged(b"FRAMES", b"Y" * 128)
        assert tagged[6:10] == b"\x00\x00\x01\x00"
        assert strip_id3(tagged) == b"FRAMES"

    def test_a_stray_high_bit_cannot_inflate_the_tag_length(self):
        # Bit 7 of every synchsafe byte is unused; 0x84 means 4, not 132.
        assert strip_id3(b"ID3\x03\x00\x00\x00\x00\x00\x84" + b"M" * 4 + b"FRAMES") == b"FRAMES"


class TestNarrate:
    @pytest.mark.asyncio
    async def test_joined_chunks_do_not_repeat_the_id3_tag(self, service, monkeypatch):
        async def fake_synthesize(chunk):
            return _tagged(chunk.encode("utf-8"))

        monkeypatch.setattr(service, "_synthesize", fake_synthesize)
        audio, content_type = await service.narrate("Alpha beta. Gamma delta.")
        assert b"ID3" not in audio
        assert audio == b"Alpha beta. Gamma delta."
        assert content_type == "audio/mpeg"

    @pytest.mark.asyncio
    async def test_second_play_is_served_from_cache(self, service, fake_cache, monkeypatch):
        calls = []

        async def fake_synthesize(chunk):
            calls.append(chunk)
            return _tagged(b"AUDIO")

        monkeypatch.setattr(service, "_synthesize", fake_synthesize)
        first = await service.narrate("Same article text.")
        second = await service.narrate("Same article text.")

        assert first == second == (b"AUDIO", "audio/mpeg")
        assert len(calls) == 1
        assert fake_cache.writes and fake_cache.writes[0][0].startswith("tts:")
        assert base64.b64decode(fake_cache.store[fake_cache.writes[0][0]]) == b"AUDIO"

    @pytest.mark.asyncio
    async def test_a_cache_write_failure_still_returns_audio(
        self, service, monkeypatch
    ):
        async def fake_synthesize(chunk):
            return _tagged(b"AUDIO")

        async def boom(key, value, ttl_seconds):
            raise ConnectionError("redis down")

        monkeypatch.setattr(service, "_synthesize", fake_synthesize)
        monkeypatch.setattr(tts_module.cache_service, "set", boom)
        assert (await service.narrate("Readable despite a dead cache."))[0] == b"AUDIO"

    @pytest.mark.asyncio
    async def test_text_with_nothing_to_say_is_refused(self, service):
        with pytest.raises(TTSUnavailable):
            await service.narrate("   ")

    @pytest.mark.asyncio
    async def test_unconfigured_service_refuses_instead_of_calling_a_provider(self, monkeypatch):
        monkeypatch.setattr(TTSService, "configured", property(lambda self: False))
        svc = TTSService()
        with pytest.raises(TTSUnavailable):
            await svc.narrate("Anything at all.")

    @pytest.mark.asyncio
    async def test_missing_client_is_reported_as_unavailable(self, service):
        # No client means the checkout has no usable credential for this
        # provider; that must surface as a clear error, never a silent success.
        service._provider = "openai"
        service._client = None
        with pytest.raises(TTSUnavailable):
            await service.narrate("Some narration text.")

    def test_cache_key_is_bound_to_provider_voice_and_text(self, monkeypatch):
        monkeypatch.setattr(TTSService, "configured", property(lambda self: True))
        svc = TTSService()
        first = svc.cache_key("text")
        assert first == svc.cache_key("text")
        assert first != svc.cache_key("other text")


class TestProviderSelection:
    def test_gemini_ignores_an_openai_voice_name(self, monkeypatch):
        monkeypatch.setattr(TTSService, "configured", property(lambda self: True))
        svc = TTSService()
        monkeypatch.setattr(svc, "_provider", "gemini")
        assert svc._voice == "Kore"

    def test_gemini_swaps_in_its_own_default_model(self, monkeypatch):
        monkeypatch.setattr(TTSService, "configured", property(lambda self: True))
        svc = TTSService()
        monkeypatch.setattr(svc, "_provider", "gemini")
        assert svc._model == "gemini-2.5-flash-preview-tts"

    def test_gemini_keeps_an_explicit_prebuilt_voice(self, monkeypatch):
        monkeypatch.setattr(TTSService, "configured", property(lambda self: True))
        svc = TTSService()
        monkeypatch.setattr(svc, "_provider", "gemini")
        monkeypatch.setattr(tts_module.settings, "TTS_VOICE", "Puck")
        assert svc._voice == "Puck"

    def test_unsupported_provider_is_not_configured(self, monkeypatch):
        # Uses the real property: a vendor with no implementation here must be
        # rejected even when a key is present, so the route reports 501.
        svc = TTSService()
        monkeypatch.setattr(svc, "_provider", "elevenlabs")
        assert svc.configured is False

    def test_local_needs_no_api_key_but_needs_its_model_files(self, tmp_path, monkeypatch):
        svc = TTSService()
        monkeypatch.setattr(svc, "_provider", "local")
        monkeypatch.setattr(tts_module.settings, "TTS_LOCAL_MODEL_PATH", str(tmp_path / "model.onnx"))
        monkeypatch.setattr(
            tts_module.settings, "TTS_LOCAL_VOICES_PATH", str(tmp_path / "voices.bin")
        )

        assert svc.configured is False, "missing files must report 501, not a runtime error"

        model = tmp_path / "model.onnx"
        voices = tmp_path / "voices.bin"
        model.write_bytes(b"onnx")
        voices.write_bytes(b"voices")
        assert svc.configured is True

    def test_local_voice_and_model_come_from_local_settings(self, monkeypatch):
        svc = TTSService()
        monkeypatch.setattr(svc, "_provider", "local")
        monkeypatch.setattr(tts_module.settings, "TTS_LOCAL_VOICE", "am_michael")
        monkeypatch.setattr(tts_module.settings, "TTS_LOCAL_MODEL_PATH", "/models/kokoro-v1.0.onnx")
        # The OpenAI voice name is meaningless to Kokoro and must not leak in.
        monkeypatch.setattr(tts_module.settings, "TTS_VOICE", "alloy")
        assert svc._voice == "am_michael"
        assert svc._model == "kokoro-v1.0.onnx"


class TestSniffContentType:
    def test_riff_is_reported_as_wav(self):
        assert sniff_content_type(_wav(b"\x00\x00" * 8), "audio/mpeg") == "audio/wav"

    def test_id3_tagged_audio_is_reported_as_mpeg(self):
        assert sniff_content_type(_tagged(b"\xff\xfb\x90\x00"), "audio/wav") == "audio/mpeg"

    def test_bare_mpeg_frame_sync_is_reported_as_mpeg(self):
        assert sniff_content_type(b"\xff\xfb\x90\x00rest", "audio/wav") == "audio/mpeg"

    def test_unrecognised_bytes_fall_back_to_the_provider_default(self):
        assert sniff_content_type(b"raw pcm", "audio/wav") == "audio/wav"


class TestAudioExtension:
    def test_wav_and_mp3_map_to_their_own_extension(self):
        assert audio_extension("audio/wav") == ".wav"
        assert audio_extension("audio/mpeg") == ".mp3"


class TestJoinAudio:
    def test_wav_chunks_are_joined_by_frame_not_by_header(self):
        # Appending two whole files would play the first chunk's silence twice and
        # leave an unparseable second header mid-stream.
        joined = join_wav([_wav(b"\x01\x00" * 4), _wav(b"\x02\x00" * 6)])
        rate, channels, width, frames = wav_frames(joined)
        assert (rate, channels, width) == (24000, 1, 2)
        assert frames == b"\x01\x00" * 4 + b"\x02\x00" * 6

    def test_chunks_with_different_sample_rates_are_refused(self):
        with pytest.raises(TTSUnavailable):
            join_wav([_wav(b"\x00\x00" * 4, rate=24000), _wav(b"\x00\x00" * 4, rate=16000)])

    def test_join_dispatches_on_the_container(self):
        wavs = [_wav(b"\x01\x00" * 2), _wav(b"\x02\x00" * 2)]
        assert join_audio(wavs, "audio/wav")[:4] == b"RIFF"
        mp3s = [_tagged(b"\xff\xfbA"), _tagged(b"\xff\xfbB")]
        assert join_audio(mp3s, "audio/mpeg") == b"\xff\xfbA\xff\xfbB"

    def test_headerless_pcm_is_appended_as_is(self):
        # Gemini may answer with audio/L16, which has no framing to repair.
        parts = [b"\x00\x01\x02", b"\x03\x04\x05"]
        assert join_audio(parts, "audio/wav") == b"\x00\x01\x02\x03\x04\x05"

    def test_a_single_wav_chunk_keeps_exactly_one_header(self):
        only = _wav(b"\x01\x00" * 2)
        joined = join_audio([only], "audio/wav")
        assert joined.count(b"RIFF") == 1
        assert wav_frames(joined)[3] == b"\x01\x00" * 2


class _FakeKokoro:
    """Stands in for kokoro_onnx.Kokoro: returns a tone instead of speech."""

    def __init__(self, level: float = 1.0, samples: int = 24000):
        self.calls = []
        self.level = level
        self.samples = samples

    def create(self, text, voice=None, speed=1.0, lang="en-us"):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        # A second of audio per chunk, so MP3 frames actually come out: the encoder
        # buffers a frame's worth of samples before it can emit one.
        tone = [0.0, 0.5, -0.5, 0.25]
        return [tone[index % len(tone)] * self.level for index in range(self.samples)], 24000


class TestLocalSynthesis:
    @pytest.fixture
    def local_service(self, monkeypatch):
        monkeypatch.setattr(TTSService, "configured", property(lambda self: True))
        svc = TTSService()
        monkeypatch.setattr(svc, "_provider", "local")
        monkeypatch.setattr(tts_module.settings, "TTS_LOCAL_VOICE", "af_heart")
        # Keep assertions on WAV independent of whether lameenc is installed.
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: False)
        return svc

    @pytest.mark.asyncio
    async def test_engine_output_becomes_playable_wav(self, local_service, fake_cache, monkeypatch):
        engine = _FakeKokoro()
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: engine)

        audio, content_type = await local_service.narrate("Ethiopia advances toward the capital.")

        assert content_type == "audio/wav"
        rate, channels, width, frames = wav_frames(audio)
        assert (rate, channels, width) == (24000, 1, 2)
        assert frames  # real samples, not silence-free emptiness
        assert engine.calls[0]["voice"] == "af_heart"

    @pytest.mark.asyncio
    async def test_long_text_is_synthesised_in_chunks(self, local_service, fake_cache, monkeypatch):
        engine = _FakeKokoro()
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: engine)
        monkeypatch.setattr(tts_module.settings, "TTS_CHUNK_CHARS", 40)

        text = " ".join(f"Sentence number {i} adds a little more weight." for i in range(6))
        audio, _ = await local_service.narrate(text)

        assert len(engine.calls) > 1
        _, _, _, frames = wav_frames(audio)
        # Every chunk contributes its samples, so the join must not drop any.
        # 16-bit mono means two bytes per sample.
        assert len(frames) == engine.samples * 2 * len(engine.calls)

    @pytest.mark.asyncio
    async def test_second_play_is_served_from_cache(self, local_service, fake_cache, monkeypatch):
        engine = _FakeKokoro()
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: engine)

        first = await local_service.narrate("Cached briefing.")
        second = await local_service.narrate("Cached briefing.")

        assert first == second
        assert len(engine.calls) == 1

    @pytest.mark.asyncio
    async def test_engine_failure_is_reported_as_unavailable(self, local_service, monkeypatch):
        class Broken:
            def create(self, *args, **kwargs):
                raise RuntimeError("onnxruntime exploded")

        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: Broken())
        with pytest.raises(TTSUnavailable):
            await local_service.narrate("This briefing cannot be read aloud.")

    @pytest.mark.asyncio
    async def test_mp3_encoder_failure_still_serves_audio(self, local_service, monkeypatch):
        engine = _FakeKokoro()
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: engine)
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: True)
        monkeypatch.setattr(
            tts_module,
            "encode_mp3",
            lambda wav, quality: (_ for _ in ()).throw(RuntimeError("no encoder here")),
        )

        audio, content_type = await local_service.narrate("Narration without an encoder.")
        assert content_type == "audio/wav"
        assert audio[:4] == b"RIFF"

    @pytest.mark.asyncio
    async def test_quiet_engine_output_is_normalised_into_the_wav(
        self, local_service, monkeypatch, fake_cache
    ):
        """A quiet engine must not ship a quiet file: listeners call that muffled."""
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: _FakeKokoro(level=0.02))

        audio, content_type = await local_service.narrate("A quiet briefing.")

        assert content_type == "audio/wav"
        _rate, _channels, _width, frames = wav_frames(audio)
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        assert rms > 0.05  # the 0.02-level input would have sat near 0.01
        assert float(np.abs(samples).max()) <= 1.0


class TestMp3Encoding:
    def test_wave_is_encoded_to_an_mp3_stream(self):
        pytest.importorskip("lameenc")
        pytest.importorskip("numpy")
        from app.services.tts_service import encode_mp3

        pcm = b"\x00\x00\x40\x00" * 480  # 0.05s of 24 kHz mono 16-bit audio
        encoded = encode_mp3(_wav(pcm), 7)

        assert encoded[:2] == b"\xff\xf3"  # MPEG-2 Layer III frame sync
        assert len(encoded) > 0

    def test_encoder_receives_int16_pcm_not_float_bytes(self, monkeypatch):
        """Regression: float32 bytes decode to clipped noise, not speech.

        lameenc reads its input as signed 16-bit samples. When it was handed
        float32 bytes it consumed each float's bit pattern as two samples, which
        produced right-length audio that peaked at 0 dBFS with thousands of clipped
        frames — audible as garbled, muffled speech.
        """
        np = pytest.importorskip("numpy")
        import types

        captured = {}

        class _Encoder:
            def set_in_sample_rate(self, rate):
                captured["rate"] = rate

            def set_channels(self, channels):
                captured["channels"] = channels

            def set_quality(self, quality):
                captured["quality"] = quality

            def encode(self, data):
                captured["pcm"] = data
                return bytearray(b"\xff\xf3")

            def flush(self):
                return bytearray()

        monkeypatch.setitem(
            __import__("sys").modules, "lameenc", types.SimpleNamespace(Encoder=_Encoder)
        )
        from app.services.tts_service import encode_mp3

        samples = (np.sin(np.linspace(0, 100, 480)) * 8000).astype("<i2")
        encode_mp3(_wav(samples.tobytes()), 7)

        passed = np.frombuffer(captured["pcm"], dtype="<i2")
        assert len(passed) == len(samples), "encoder got the wrong number of samples"
        # Round-tripping through float32 would quantise these away from their
        # original values; an exact int16 hand-off keeps every sample intact.
        assert np.array_equal(passed, samples)

    def test_non_16_bit_audio_is_refused(self):
        pytest.importorskip("lameenc")
        from app.services.tts_service import encode_mp3

        with pytest.raises(TTSUnavailable):
            encode_mp3(_wav(b"\x00" * 100, width=1), 7)


class TestLoudnessNormalisation:
    """Kokoro output sits near -22 dBFS RMS, which reads as muffled when quiet."""

    @staticmethod
    def _rms_dbfs(samples):
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32)
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        return 20.0 * np.log10(rms)

    def test_quiet_audio_is_raised_toward_the_target(self):
        np = pytest.importorskip("numpy")
        from app.services.tts_service import normalize_loudness

        quiet = (np.sin(np.linspace(0, 200, 4800)) * 0.05).astype(np.float32)
        assert self._rms_dbfs(quiet) < -25.0

        louder = normalize_loudness(quiet, target_dbfs=-20.0, ceiling_dbfs=-1.0)

        assert abs(self._rms_dbfs(louder) - -20.0) < 0.1

    def test_peak_ceiling_prevents_clipping(self):
        np = pytest.importorskip("numpy")
        from app.services.tts_service import normalize_loudness

        spiky = (np.sin(np.linspace(0, 200, 4800)) * 0.2).astype(np.float32)
        spiky[0] = 0.9  # a transient far above the RMS

        normalised = normalize_loudness(spiky, target_dbfs=-6.0, ceiling_dbfs=-1.0)

        assert float(np.abs(normalised).max()) <= 10 ** (-1.0 / 20.0) + 1e-6
        assert np.abs(normalised).max() < 1.0

    def test_loud_audio_is_not_pushed_into_clipping(self):
        np = pytest.importorskip("numpy")
        from app.services.tts_service import normalize_loudness

        loud = (np.sin(np.linspace(0, 200, 4800)) * 0.9).astype(np.float32)

        normalised = normalize_loudness(loud, target_dbfs=-20.0, ceiling_dbfs=-1.0)

        assert float(np.abs(normalised).max() - 1.0) < 1e-3

    def test_silence_is_left_alone(self):
        np = pytest.importorskip("numpy")
        from app.services.tts_service import normalize_loudness

        silence = np.zeros(128, dtype=np.float32)

        normalised = normalize_loudness(silence, target_dbfs=-20.0, ceiling_dbfs=-1.0)

        assert np.all(normalised == 0.0)
        assert not np.any(np.isnan(normalised))


class TestProgressiveStreaming:
    """Streaming is what removes the wait: playback starts on the first chunk."""

    @pytest.mark.asyncio
    async def test_audio_is_yielded_in_several_pieces(self, local_provider, fake_cache, monkeypatch):
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: True)
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: _FakeKokoro())
        monkeypatch.setattr(tts_module.settings, "TTS_STREAM_CHUNK_CHARS", 40)

        parts = [
            part
            async for part in local_provider.iter_audio("First sentence here. " * 12)
        ]

        assert len(parts) > 1, "a long text must not arrive as one lump"
        assert all(part for part in parts)

    @pytest.mark.asyncio
    async def test_a_cached_recording_is_yielded_whole(
        self, local_provider, fake_cache, monkeypatch
    ):
        cached_audio = _tagged(b"\xff\xf3" + b"\x00" * 64)
        fake_cache.store[local_provider.cache_key(to_speech_text("Cached text.", 20000))] = (
            base64.b64encode(cached_audio).decode("ascii")
        )

        parts = [part async for part in local_provider.iter_audio("Cached text.")]

        assert parts == [cached_audio]

    @pytest.mark.asyncio
    async def test_a_finished_stream_is_cached_for_next_time(
        self, local_provider, fake_cache, monkeypatch
    ):
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: True)
        monkeypatch.setattr(tts_module.settings, "TTS_STREAM_CHUNK_CHARS", 40)
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: _FakeKokoro())
        text = "Streamed once. " * 12

        async for _ in local_provider.iter_audio(text):
            pass

        assert any(key.startswith("tts:") for key in fake_cache.store)

    @pytest.mark.asyncio
    async def test_the_stream_matches_one_pass_encoding(self, local_provider, fake_cache, monkeypatch):
        """One encoder for the whole recording, not one per chunk.

        A per-chunk encoder flushes at every seam, and each flush leaves a half frame
        behind, so the joined stream would click every time it crosses a boundary.
        Encoding the same PCM in one pass must give byte-identical output.
        """
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: True)
        monkeypatch.setattr(tts_module.settings, "TTS_STREAM_FIRST_CHUNK_CHARS", 40)
        monkeypatch.setattr(tts_module.settings, "TTS_STREAM_CHUNK_CHARS", 60)

        voiced = []

        async def _wav_piece(self, chunk):
            voiced.append(_wav(b"\x10\x27" * (2000 + 500 * len(voiced))))
            return voiced[-1]

        monkeypatch.setattr(TTSService, "_synthesize_local_wav", _wav_piece)

        streamed = b"".join(
            [part async for part in local_provider.iter_audio("Some briefing. " * 30)]
        )

        assert len(voiced) > 2, "the text really was split"
        one_pass = encode_mp3(_wav(b"".join(wav_frames(piece)[3] for piece in voiced)), 7)
        assert streamed == one_pass, "streaming and one-pass encoding must agree byte for byte"

    @pytest.mark.asyncio
    async def test_only_the_first_chunk_of_a_hosted_provider_keeps_its_tag(
        self, service, fake_cache, monkeypatch
    ):
        """Hosted providers return a complete tagged file per call, so the seam needs repair."""
        monkeypatch.setattr(service, "_provider", "openai")
        monkeypatch.setattr(tts_module.settings, "TTS_STREAM_FIRST_CHUNK_CHARS", 40)

        async def _tagged_piece(self, chunk):
            return _tagged(b"\xff\xfb" + b"\x00" * 32)

        monkeypatch.setattr(TTSService, "_synthesize", _tagged_piece)

        parts = [part async for part in service.iter_audio("One two three. " * 30)]

        assert parts[0].startswith(b"ID3"), "the stream opens with a tagged chunk"
        assert all(not part.startswith(b"ID3") for part in parts[1:]), "no repeated tags mid-stream"

    def test_only_local_mp3_is_announced_as_streamable(self, service, monkeypatch):
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: True)
        monkeypatch.setattr(service, "_provider", "local")
        assert service.streams_progressively is True

        monkeypatch.setattr(service, "_provider", "openai")
        assert service.streams_progressively is False, "hosted clips arrive already flushed"

        monkeypatch.setattr(service, "_provider", "gemini")
        assert service.streams_progressively is False, "WAV headers declare a length up front"

        monkeypatch.setattr(service, "_provider", "local")
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: False)
        assert service.streams_progressively is False, "local falls back to WAV without lameenc"


class TestSharedSynthesis:
    """Duplicate work is what turns one clip into three, so it has to be shared."""

    @pytest.mark.asyncio
    async def test_listeners_asking_at_once_are_served_by_one_run(
        self, local_provider, fake_cache, monkeypatch
    ):
        calls = []

        async def fake_synthesize_with_provider(chunk):
            calls.append(chunk)
            await asyncio.sleep(0.02)
            return _tagged(b"\xff\xf3" + b"\x00" * 32)

        monkeypatch.setattr(
            local_provider, "_synthesize_with_provider", fake_synthesize_with_provider
        )

        results = await asyncio.gather(
            *(local_provider.narrate("One clip, three listeners.") for _ in range(3))
        )

        assert len(calls) == 1, "the same text must not be voiced three times"
        assert results[0] == results[1] == results[2]

    @pytest.mark.asyncio
    async def test_a_listener_leaving_does_not_cancel_the_shared_run(
        self, local_provider, fake_cache, monkeypatch
    ):
        calls = []

        async def slow_provider(chunk):
            calls.append(chunk)
            await asyncio.sleep(0.15)
            return _tagged(b"\xff\xf3" + b"\x00" * 32)

        monkeypatch.setattr(local_provider, "_synthesize_with_provider", slow_provider)

        staying = asyncio.ensure_future(local_provider.narrate("Stay with me."))
        leaving = asyncio.ensure_future(local_provider.narrate("Stay with me."))
        await asyncio.sleep(0.02)
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving

        audio, _ = await staying
        assert audio

        assert len(calls) == 1
        # The abandoned listener must not have cost anyone their narration.
        assert local_provider.cache_key(to_speech_text("Stay with me.", 20000)) in fake_cache.store

    @pytest.mark.asyncio
    async def test_only_one_clip_is_voiced_at_a_time(self, local_provider, fake_cache, monkeypatch):
        monkeypatch.setattr(tts_module.settings, "TTS_MAX_CONCURRENT_SYNTHESIS", 1)
        live = 0
        peak = 0

        async def watched_provider(chunk):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.02)
            live -= 1
            return _tagged(b"\xff\xf3" + b"\x00" * 32)

        monkeypatch.setattr(local_provider, "_synthesize_with_provider", watched_provider)

        await asyncio.gather(
            *(local_provider.narrate(f"Different clip number {index}.") for index in range(4))
        )

        assert peak == 1, f"expected one clip at a time, saw {peak} at once"

    @pytest.mark.asyncio
    async def test_two_streams_never_run_the_model_together(
        self, local_provider, fake_cache, monkeypatch
    ):
        """The play button streams, so the cap has to hold on that path too.

        Sharing a chunk is opportunistic — a stream that falls behind can start its
        own run of a chunk the other one already finished. The guarantee that keeps
        the CPU bounded is that only one run happens at a time.
        """
        monkeypatch.setattr(tts_module, "_mp3_available", lambda: True)
        monkeypatch.setattr(tts_module.settings, "TTS_MAX_CONCURRENT_SYNTHESIS", 1)
        live = 0
        peak = 0

        async def counted(chunk):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.02)
            live -= 1
            return _wav(b"\x00" * 64)

        monkeypatch.setattr(local_provider, "_synthesize_local_wav", counted)

        async def collect():
            return [part async for part in local_provider.iter_audio("Shared stream. " * 12)]

        first, second = await asyncio.gather(collect(), collect())

        assert first == second, "both listeners should hear the same recording"
        assert peak == 1, f"expected one model run at a time, saw {peak} at once"

    @pytest.mark.asyncio
    async def test_a_warm_up_stands_aside_for_a_listener(self, service, fake_cache, monkeypatch):
        monkeypatch.setattr(tts_module.settings, "TTS_MAX_CONCURRENT_SYNTHESIS", 1)
        released = asyncio.Event()

        async def blocking_provider(chunk):
            await released.wait()
            return _tagged(b"\xff\xf3" + b"\x00" * 32)

        monkeypatch.setattr(service, "_synthesize_with_provider", blocking_provider)

        listener = asyncio.ensure_future(service.narrate("The listener speaks first."))
        await asyncio.sleep(0.02)

        assert await service.warm_narration("Something to pre-warm.") is None, (
            "pre-warming must not queue in front of a listener"
        )

        released.set()
        await listener

    @pytest.mark.asyncio
    async def test_a_warm_up_runs_when_the_cores_are_free(self, service, fake_cache, monkeypatch):
        async def quick_provider(chunk):
            return _tagged(b"\xff\xf3" + b"\x00" * 32)

        monkeypatch.setattr(service, "_synthesize_with_provider", quick_provider)

        warmed = await service.warm_narration("Nothing else is speaking.")

        assert warmed is not None


class TestOnnxThreadCap:
    """One narration must not be allowed to take every core on the host."""

    @pytest.fixture
    def captured(self, monkeypatch):
        import kokoro_onnx
        import onnxruntime as ort

        seen = {}

        class FakeSession:
            def __init__(self, model_path, sess_options=None, providers=None, **kwargs):
                seen["threads"] = sess_options.intra_op_num_threads
                seen["inter_threads"] = sess_options.inter_op_num_threads
                seen["providers"] = providers

        class FakeKokoro:
            def __init__(self, model_path, voices_path):
                # Goes through the package attribute the service patches, so this
                # only works if the patch is actually in place.
                seen["session"] = kokoro_onnx.create_session(model_path)

        monkeypatch.setattr(ort, "InferenceSession", FakeSession)
        monkeypatch.setattr(kokoro_onnx, "Kokoro", FakeKokoro)
        seen["original_create_session"] = kokoro_onnx.create_session
        return seen

    def test_the_session_gets_a_bounded_thread_pool(self, captured, monkeypatch):
        monkeypatch.setattr(tts_module.settings, "TTS_ONNX_THREADS", 3)

        tts_module._build_local_engine()

        assert captured["threads"] == 3
        assert captured["inter_threads"] == 1
        assert captured["providers"], "execution providers are still resolved"

    def test_zero_leaves_the_library_default(self, captured, monkeypatch):
        monkeypatch.setattr(tts_module.settings, "TTS_ONNX_THREADS", 0)

        tts_module._build_local_engine()

        assert captured["threads"] == 0, "ORT reads 0 as 'decide for me'"

    def test_the_patch_does_not_outlive_model_loading(self, captured, monkeypatch):
        import kokoro_onnx

        monkeypatch.setattr(tts_module.settings, "TTS_ONNX_THREADS", 3)

        tts_module._build_local_engine()

        assert kokoro_onnx.create_session is captured["original_create_session"], (
            "the thread cap must not leak into other users of kokoro_onnx"
        )

    def test_the_cap_is_restored_even_if_loading_fails(self, captured, monkeypatch):
        import kokoro_onnx

        monkeypatch.setattr(tts_module.settings, "TTS_ONNX_THREADS", 3)
        original = captured["original_create_session"]

        def explode(*args, **kwargs):
            raise RuntimeError("model file missing")

        monkeypatch.setattr(kokoro_onnx, "Kokoro", explode)

        with pytest.raises(RuntimeError):
            tts_module._build_local_engine()

        assert kokoro_onnx.create_session is original


class TestStartupPreload:
    """The model is loaded at boot so the first listener does not pay for it."""

    @pytest.mark.asyncio
    async def test_preloading_local_loads_the_engine(self, local_provider, monkeypatch):
        loaded = []
        monkeypatch.setattr(tts_module, "_load_local_engine", lambda: loaded.append(True))
        voiced = []

        async def fake_wav(chunk):
            voiced.append(chunk)
            return b"RIFF"

        monkeypatch.setattr(local_provider, "_synthesize_local_wav", fake_wav)

        assert await local_provider.preload_local() is True
        assert loaded, "weights are read outside the request path"
        assert voiced, "one throwaway inference runs at boot, so the listener's first chunk does not pay for onnxruntime's lazy init"

    @pytest.mark.asyncio
    async def test_preloading_is_skipped_for_hosted_providers(self, service, monkeypatch):
        monkeypatch.setattr(service, "_provider", "openai")
        monkeypatch.setattr(
            tts_module, "_load_local_engine", lambda: pytest.fail("must not load a local model")
        )

        assert await service.preload_local() is False

    @pytest.mark.asyncio
    async def test_a_failed_preload_is_swallowed(self, local_provider, monkeypatch):
        def explode():
            raise RuntimeError("model file missing")

        monkeypatch.setattr(tts_module, "_load_local_engine", explode)

        assert await local_provider.preload_local() is False


class TestStreamChunking:
    """The first chunk is the whole latency budget; the rest just keep up."""

    def test_the_lead_in_is_shorter_than_the_chunks_behind_it(self):
        text = " ".join(f"Sentence number {n} of the briefing runs on." for n in range(40))

        chunks = stream_chunks(text, 100, 800)

        assert len(chunks[0]) <= 100, "the listener waits on this one"
        assert all(len(chunk) <= 800 for chunk in chunks), "later chunks fit their budget"

    def test_every_word_is_kept_exactly_once(self):
        text = " ".join(f"Sentence number {n} of the briefing runs on." for n in range(40))

        chunks = stream_chunks(text, 100, 800)

        assert " ".join(chunks).split() == text.split()

    def test_short_text_is_a_single_chunk(self):
        chunks = stream_chunks("A short line.", 100, 800)

        assert chunks == ["A short line."]

    def test_a_monster_sentence_is_cut_on_word_boundaries(self):
        chunks = stream_chunks(" ".join(["word"] * 900), 100, 800)

        assert all(len(chunk) <= 800 for chunk in chunks)
        assert " ".join(chunks).split() == ["word"] * 900

    def test_no_text_produces_no_chunks(self):
        assert stream_chunks("", 100, 800) == []

    @pytest.mark.parametrize("first_chars, rest_chars", [(0, 800), (100, 0), (-1, -1)])
    def test_nonpositive_sizes_are_rejected(self, first_chars, rest_chars):
        with pytest.raises(ValueError):
            stream_chunks("Some text.", first_chars, rest_chars)

    def test_the_lead_in_is_the_whole_text_when_it_is_long_enough(self):
        text = " ".join(f"Sentence number {n} of the briefing runs on." for n in range(6))

        chunks = stream_chunks(text, 5000, 800)

        assert len(chunks) == 1 and chunks[0] == text

    def test_the_refill_after_the_opener_is_cut_smaller_than_the_opener(self):
        # A short chunk costs about as much wall time as it yields audio, while a
        # full-size one yields roughly twice that, so the chunks that have to land
        # while the opener is still playing are the ones that get cut down.
        text = " ".join(f"Sentence number {n} of the briefing runs on." for n in range(40))

        chunks = stream_chunks(text, 150, 300)

        assert len(chunks) > 4, "this text is long enough to need a ramp at all"
        assert len(chunks[1]) < len(chunks[0]), "the first refill must land before the opener runs out"
        assert len(chunks[2]) <= 300, "the second refill still fits the budget"

    def test_only_the_refills_are_ramped_and_later_chunks_use_the_full_size(self):
        # Ramping the whole text would drop every later chunk back to the slow
        # per-chunk rate, which on wall-clock time loses to not ramping at all.
        text = " ".join(["word"] * 900)

        chunks = stream_chunks(text, 150, 300)

        assert len(chunks[1]) < 300, "the first refill stays small"
        assert len(chunks[3]) > 200, "everything after the refills is cut at the full size"
        assert " ".join(chunks).split() == ["word"] * 900


class TestNarrationTextBuilders:
    """The warmer and the audio routes must hash identical text to share cache."""

    class _Article:
        title = "Headline"
        content = "<p>Body text.</p>"

    def test_article_text_includes_headline_and_body(self):
        from app.services.tts_service import article_narration_text

        assert article_narration_text(self._Article()) == "Headline. <p>Body text.</p>"

    def test_article_text_tolerates_missing_body(self):
        from app.services.tts_service import article_narration_text

        article = self._Article()
        article.content = None

        assert article_narration_text(article) == "Headline. "

    def test_event_text_falls_back_to_a_generic_title(self):
        from app.services.tts_service import event_narration_text

        assert event_narration_text(None, "A summary.") == "Event briefing. A summary."