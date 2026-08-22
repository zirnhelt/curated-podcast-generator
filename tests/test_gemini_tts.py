"""Unit tests for gemini_tts.py and TTS provider/credits resolution — no API keys."""

import base64
import wave

import pytest

import gemini_tts
from gemini_tts import (
    build_transcript,
    _build_payload,
    _synthesize_chunk,
    generate_gemini_tts_for_section,
    TRANSCRIPT_CHAR_LIMIT,
)
from config_loader import (
    get_gemini_voice_for_host,
    strip_stage_directions,
    render_credits_text,
)


SEGS = [
    {"speaker": "riley", "text": "Welcome back to the show.", "gap_ms": None},
    {"speaker": "casey", "text": "Sure. Another banner day in Quesnel.", "gap_ms": None},
]


@pytest.fixture(autouse=True)
def _reset_gemini_module_state():
    """gemini_tts keeps the render deadline, model pin and pending degradations
    in module globals — leaking any of them across tests would make a later test
    silently skip attempts or inherit another test's model."""
    gemini_tts.set_render_deadline(None)
    gemini_tts.set_model_override(None)
    gemini_tts.drain_degradations()
    yield
    gemini_tts.set_render_deadline(None)
    gemini_tts.set_model_override(None)
    gemini_tts.drain_degradations()


class TestBuildTranscript:
    def test_speaker_labels_use_display_names(self):
        transcript = build_transcript(SEGS)
        assert transcript.startswith("Riley: ")
        assert "\nCasey: " in transcript

    def test_pronunciation_applied(self):
        transcript = build_transcript(SEGS)
        assert "Kwenell" in transcript
        assert "Quesnel" not in transcript

    def test_stage_directions_pass_through(self):
        segs = [{"speaker": "casey", "text": "(wry) Sure it will.", "gap_ms": None}]
        assert "(wry)" in build_transcript(segs)


class TestBuildPayload:
    def test_two_speaker_config(self):
        payload = _build_payload(SEGS)
        cfg = payload["generationConfig"]["speechConfig"]["multiSpeakerVoiceConfig"]
        voices = {
            c["speaker"]: c["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
            for c in cfg["speakerVoiceConfigs"]
        }
        assert voices == {
            "Riley": get_gemini_voice_for_host("riley"),
            "Casey": get_gemini_voice_for_host("casey"),
        }
        assert payload["generationConfig"]["responseModalities"] == ["AUDIO"]

    def test_single_speaker_uses_plain_voice_config(self):
        payload = _build_payload([SEGS[0]])
        speech = payload["generationConfig"]["speechConfig"]
        assert "multiSpeakerVoiceConfig" not in speech
        voice = speech["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
        assert voice == get_gemini_voice_for_host("riley")

    def test_single_speaker_is_not_asked_for_a_conversation(self):
        """A one-turn section (the cold open) is not a conversation, and asking
        for one 'between Riley' is a malformed request — it was also the call
        that failed most often in the week of 2026-08-01."""
        prompt = _build_payload([SEGS[0]])["contents"][0]["parts"][0]["text"]
        # Not a blanket "conversation between" check — the style prompt legitimately
        # describes the show as a conversation. It's the *instruction* that was malformed.
        assert "conversation between Riley:" not in prompt
        assert "read aloud by Riley" in prompt

    def test_two_speakers_still_asked_for_a_conversation(self):
        prompt = _build_payload(SEGS)["contents"][0]["parts"][0]["text"]
        assert "conversation between Riley and Casey" in prompt

    def test_three_speakers_raises(self):
        segs = SEGS + [{"speaker": "guest", "text": "Hi.", "gap_ms": None}]
        with pytest.raises(ValueError):
            _build_payload(segs)

    def test_style_prompt_prefixed(self):
        prompt = _build_payload(SEGS)["contents"][0]["parts"][0]["text"]
        # Style prompt from config/prompts.json leads, transcript follows
        assert prompt.index("community-radio") < prompt.index("Riley: ")

    def test_seed_and_temperature_pinned_for_voice_consistency(self):
        cfg = _build_payload(SEGS)["generationConfig"]
        assert cfg["seed"] == gemini_tts.GEMINI_TTS_SEED
        assert cfg["temperature"] == gemini_tts.GEMINI_TTS_TEMPERATURE

    def test_no_context_block_by_default(self):
        prompt = _build_payload(SEGS)["contents"][0]["parts"][0]["text"]
        assert "CONTEXT" not in prompt

    def test_continuation_note_prepended_before_transcript(self):
        prompt = _build_payload(SEGS, continuing=True)["contents"][0]["parts"][0]["text"]
        assert gemini_tts.CONTINUATION_NOTE in prompt
        assert prompt.index(gemini_tts.CONTINUATION_NOTE) < prompt.index("Riley: ")

    def test_continuation_note_carries_no_quotable_dialogue(self):
        """Regression (2026-08-17): the cold open aired twice.

        This block used to be the previous section's verbatim transcript,
        labelled 'do not repeat'. Gemini read it aloud, so the welcome section
        opened by re-speaking the whole cold open. Anything speakable here is a
        candidate for synthesis, so the note must be a directive only — nothing
        a listener could recognise as a line from the show.
        """
        prompt = _build_payload(SEGS, continuing=True)["contents"][0]["parts"][0]["text"]
        # The only speaker-tagged lines in the prompt are this call's own turns.
        assert prompt.count("Riley: ") == 1
        assert prompt.count("Casey: ") == 1
        # `continuing` is a flag, so there is no channel for prior text at all.
        with pytest.raises(TypeError):
            _build_payload(SEGS, context_tail="Casey: ...earlier line.")


class TestRetryLadderShape:
    """Each rung must change *what is asked*, not just the sampling seed —
    finishReason:OTHER comes back with zero output tokens, so re-asking the same
    question cannot fix it (2026-08-05: two reseeded attempts, identical 272
    tokens)."""

    def _prompt(self, rung, continuing=True):
        segs = [{"speaker": "casey", "text": "(wry) Sure it will.", "gap_ms": None}]
        return _build_payload(segs, continuing=continuing, rung=rung)["contents"][0]["parts"][0]["text"]

    def test_first_rung_is_the_full_quality_request(self):
        rung = gemini_tts.RETRY_LADDER[0]
        assert (rung.keep_context, rung.keep_style, rung.keep_cues) == (True, True, True)
        assert rung.backoff_s == 0
        assert not rung.fallback_model

    def test_every_rung_sheds_something_or_changes_model(self):
        first = gemini_tts.RETRY_LADDER[0]
        for rung in gemini_tts.RETRY_LADDER[1:]:
            differs = rung.fallback_model != first.fallback_model or (
                rung.keep_context, rung.keep_style, rung.keep_cues
            ) != (first.keep_context, first.keep_style, first.keep_cues)
            assert differs, f"rung {rung} re-asks the identical question"

    def test_backoff_outlasts_the_observed_failure_windows(self):
        """The 5s/10s ladder this replaces always died inside a capacity window
        that lasted one to three minutes."""
        assert sum(r.backoff_s for r in gemini_tts.RETRY_LADDER) >= 180

    def test_dropping_context_removes_the_context_block(self):
        rung = gemini_tts._Rung(0, False, True, True, False)
        assert "already spoken" not in self._prompt(rung)

    def test_dropping_style_removes_the_style_prompt(self):
        rung = gemini_tts._Rung(0, True, False, True, False)
        assert "community-radio" not in self._prompt(rung)

    def test_dropping_cues_strips_stage_directions(self):
        # Style dropped too, so the only possible source of "(wry)" is the
        # transcript — the style prompt names the cue when explaining it.
        rung = gemini_tts._Rung(0, True, False, False, False)
        prompt = self._prompt(rung)
        assert "(wry)" not in prompt
        assert "Sure it will." in prompt

    def test_cues_survive_when_the_rung_keeps_them(self):
        rung = gemini_tts._Rung(0, True, False, True, False)
        assert "(wry)" in self._prompt(rung)

    def test_fallback_model_tried_before_a_bare_transcript(self):
        """Voices are pinned by speechConfig on every rung, so a model change
        keeps the hosts sounding like themselves while a stripped prompt loses
        the direction — reach for the model first."""
        ladder = gemini_tts.RETRY_LADDER
        first_fallback_model = next(i for i, r in enumerate(ladder) if r.fallback_model)
        first_bare = next(
            i for i, r in enumerate(ladder)
            if not r.keep_style and not r.keep_cues
        )
        assert first_fallback_model < first_bare


class TestModelEnvResolution:
    """GEMINI_TTS_MODEL is resolved once at import time, so reload the module
    under each env value and reload again afterward to restore real state."""

    @pytest.fixture(autouse=True)
    def _reload_module_after(self):
        import importlib

        yield
        importlib.reload(gemini_tts)

    def _reload_with(self, monkeypatch, value):
        import importlib

        if value is None:
            monkeypatch.delenv("GEMINI_TTS_MODEL", raising=False)
        else:
            monkeypatch.setenv("GEMINI_TTS_MODEL", value)
        importlib.reload(gemini_tts)

    def test_trailing_whitespace_stripped(self, monkeypatch):
        self._reload_with(monkeypatch, "gemini-2.5-flash-preview-tts \n")
        assert gemini_tts.GEMINI_TTS_MODEL == "gemini-2.5-flash-preview-tts"

    def test_empty_falls_back_to_default(self, monkeypatch):
        self._reload_with(monkeypatch, "   ")
        assert gemini_tts.GEMINI_TTS_MODEL == "gemini-2.5-flash-preview-tts"

    def test_unset_falls_back_to_default(self, monkeypatch):
        self._reload_with(monkeypatch, None)
        assert gemini_tts.GEMINI_TTS_MODEL == "gemini-2.5-flash-preview-tts"


class TestSynthesizeGuards:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            _synthesize_chunk(SEGS)

    def test_runaway_request_raises_before_spending(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(
            gemini_tts.requests, "post",
            lambda *a, **k: pytest.fail("must not reach the network"),
        )
        huge = [{"speaker": "riley", "text": "x" * 50_000, "gap_ms": None}]
        with pytest.raises(RuntimeError, match="refusing to spend"):
            _synthesize_chunk(huge)


class TestSynthesizeRetries:
    """A 200 with no inlineData (finishReason OTHER) is a known transient
    Gemini TTS defect and must be retried like a 5xx, not fail the section."""

    # SEGS is 11 words → expects ~4.4s (400 ms/word). 200_000 bytes of s16le @
    # 24kHz is ~4.17s (ratio ~0.95) — comfortably plausible, so these fixtures
    # don't themselves trip the duration-severity check added below.
    AUDIO_PCM = b"\x00" * 200_000
    AUDIO_RESPONSE = {
        "candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "audio/L16;rate=24000",
            "data": base64.b64encode(AUDIO_PCM).decode(),
        }}]}}],
        "usageMetadata": {"totalTokenCount": 100},
    }
    NO_AUDIO_RESPONSE = {
        "candidates": [{"finishReason": "OTHER", "index": 0}],
        "usageMetadata": {"totalTokenCount": 285},
    }

    class _FakeResp:
        def __init__(self, payload, status=200):
            self.status_code = status
            self.text = str(payload)
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def _patch(self, monkeypatch, responses):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            gemini_tts.requests, "post", lambda *a, **k: responses.pop(0)
        )

    def test_no_audio_response_retried_then_succeeds(self, monkeypatch):
        responses = [
            self._FakeResp(self.NO_AUDIO_RESPONSE),
            self._FakeResp(self.AUDIO_RESPONSE),
        ]
        self._patch(monkeypatch, responses)
        pcm, rate = _synthesize_chunk(SEGS)
        assert pcm == self.AUDIO_PCM
        assert rate == 24000
        assert not responses  # both attempts consumed

    def test_no_audio_exhausts_the_ladder_and_raises(self, monkeypatch):
        rungs = len(gemini_tts.RETRY_LADDER)
        responses = [self._FakeResp(self.NO_AUDIO_RESPONSE) for _ in range(rungs)]
        self._patch(monkeypatch, responses)
        with pytest.raises(RuntimeError, match="no audio"):
            _synthesize_chunk(SEGS)
        assert not responses  # every rung consumed

    def test_no_audio_retry_perturbs_seed(self, monkeypatch):
        """A pinned seed makes generation deterministic — retrying with the
        exact same seed would just reproduce the same no-audio dud (observed
        2026-07-28: 3/3 attempts came back identical). Only the retries
        should change the seed; attempt 0 keeps the configured value."""
        seen_seeds = []
        responses = [
            self._FakeResp(self.NO_AUDIO_RESPONSE),
            self._FakeResp(self.NO_AUDIO_RESPONSE),
            self._FakeResp(self.AUDIO_RESPONSE),
        ]

        def fake_post(*args, **kwargs):
            seen_seeds.append(kwargs["json"]["generationConfig"]["seed"])
            return responses.pop(0)

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)
        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)

        _synthesize_chunk(SEGS)

        assert seen_seeds[0] == gemini_tts.GEMINI_TTS_SEED
        assert len(set(seen_seeds)) == 3  # every attempt sampled a different seed

    def test_http_500_then_success(self, monkeypatch):
        responses = [
            self._FakeResp({}, status=500),
            self._FakeResp(self.AUDIO_RESPONSE),
        ]
        self._patch(monkeypatch, responses)
        pcm, rate = _synthesize_chunk(SEGS)
        assert pcm == self.AUDIO_PCM
        assert rate == 24000

    def test_timeout_fails_fast_not_600s(self, monkeypatch):
        """A hung server must cost the (connect, read) timeout, not 10 minutes."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)
        seen = []

        def fake_post(*args, **kwargs):
            seen.append(kwargs.get("timeout"))
            return self._FakeResp(self.AUDIO_RESPONSE)

        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)
        _synthesize_chunk(SEGS)
        assert seen == [
            (gemini_tts.REQUEST_CONNECT_TIMEOUT, gemini_tts.REQUEST_READ_TIMEOUT)
        ]
        assert gemini_tts.REQUEST_READ_TIMEOUT <= 120

    def test_429_error_body_not_truncated_before_quota_details(self, monkeypatch):
        """The quota name in error.details sits past 300 chars — keep it."""
        body = (
            '{"error": {"code": 429, "message": "'
            + "x" * 400
            + '", "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}}'
        )
        responses = [
            self._FakeResp({}, status=429) for _ in range(len(gemini_tts.RETRY_LADDER))
        ]
        for r in responses:
            r.text = body
        self._patch(monkeypatch, responses)
        with pytest.raises(RuntimeError, match="GenerateRequestsPerDayPerProjectPerModel"):
            _synthesize_chunk(SEGS)

    @staticmethod
    def _audio_response(pcm_bytes: bytes) -> dict:
        return {
            "candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "audio/L16;rate=24000",
                "data": base64.b64encode(pcm_bytes).decode(),
            }}]}}],
            "usageMetadata": {"totalTokenCount": 100},
        }

    def test_severely_truncated_audio_retried_then_succeeds(self, monkeypatch):
        """2026-07-29: a retried news chunk came back 200 OK with audio at 21%
        of the expected length — a technically-successful response missing
        most of its content. That must retry, not ship as-is."""
        # SEGS expects ~4.4s; 10_000 bytes @ 24kHz/s16le is ~0.2s (ratio ~0.05).
        truncated = self._FakeResp(self._audio_response(b"\x00" * 10_000))
        responses = [truncated, self._FakeResp(self.AUDIO_RESPONSE)]
        self._patch(monkeypatch, responses)
        pcm, rate = _synthesize_chunk(SEGS)
        assert pcm == self.AUDIO_PCM
        assert not responses  # both attempts consumed

    def test_severely_truncated_audio_exhausts_retries_and_raises(self, monkeypatch):
        responses = [
            self._FakeResp(self._audio_response(b"\x00" * 10_000))
            for _ in range(len(gemini_tts.RETRY_LADDER))
        ]
        self._patch(monkeypatch, responses)
        with pytest.raises(RuntimeError, match="severely truncated"):
            _synthesize_chunk(SEGS)
        assert not responses  # every rung consumed

    def test_mild_duration_shortfall_not_retried(self, monkeypatch):
        """Ratio between the 0.80 warning line and the 0.50 severity line is
        plausible pacing variance (quick banter), not a dropped-content defect
        — must not burn a retry."""
        # ~3.1s of ~4.4s expected = ratio ~0.71
        responses = [self._FakeResp(self._audio_response(b"\x00" * 150_000))]
        self._patch(monkeypatch, responses)
        pcm, rate = _synthesize_chunk(SEGS)
        assert len(pcm) == 150_000
        assert not responses  # only one attempt made


class TestTimeBudget:
    """Five rungs of read timeouts plus backoff could otherwise hold one section
    for ~15 min, and a six-section episode would blow the 40-minute render step."""

    def _always_fails(self, monkeypatch):
        calls = []
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)

        def fake_post(*args, **kwargs):
            calls.append(kwargs["json"]["generationConfig"]["seed"])
            raise gemini_tts.requests.ConnectionError("boom")

        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)
        return calls

    def _always_rejects(self, monkeypatch):
        """Rejections, not transport failures — the ladder walks every rung."""
        calls = []
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)

        def fake_post(*args, **kwargs):
            calls.append(kwargs["json"]["generationConfig"]["seed"])
            return TestSynthesizeRetries._FakeResp(
                TestSynthesizeRetries.NO_AUDIO_RESPONSE
            )

        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)
        return calls

    def test_section_budget_stops_the_ladder_early(self, monkeypatch):
        calls = self._always_fails(monkeypatch)
        # Budget smaller than one retry's backoff plus its own cost — only
        # attempt 0 fits.
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS, budget_s=1)
        assert len(calls) == 1

    def test_full_ladder_runs_within_the_default_budget(self, monkeypatch):
        calls = self._always_rejects(monkeypatch)
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS)
        assert len(calls) == len(gemini_tts.RETRY_LADDER)

    def test_budget_reserves_the_attempt_not_just_its_backoff(self, monkeypatch):
        """A retry that cannot finish inside the budget must not be started.

        Counting only the backoff let the last attempt begin with less time
        left than its own read timeout, so it ran to the ceiling and the
        budget message overstated how many attempts had really been afforded.
        """
        calls = self._always_rejects(monkeypatch)
        # Room for the backoff (15 s) but not for the attempt behind it.
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS, budget_s=gemini_tts.REQUEST_READ_TIMEOUT)
        assert len(calls) == 1

    def test_render_deadline_blocks_further_attempts(self, monkeypatch):
        """A provider that dies after the canary passed must not eat the render
        step one section at a time."""
        calls = self._always_fails(monkeypatch)
        gemini_tts.set_render_deadline(0)  # already expired
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS)
        assert len(calls) == 1  # attempt 0 runs, nothing after it

    def test_cleared_render_deadline_does_not_bound_anything(self, monkeypatch):
        calls = self._always_rejects(monkeypatch)
        gemini_tts.set_render_deadline(None)
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS)
        assert len(calls) == len(gemini_tts.RETRY_LADDER)


class TestTransportFailureRouting:
    """A request that went unanswered is not a request that was refused.

    Read timeouts and dropped connections carry no verdict on the prompt, so
    the rungs that only reword it cannot help — and at 120 s apiece they used
    up the section budget before any model rung was reached. Every Cariboo
    Signals episode of August 2026 fell back to OpenAI through this path.
    """

    def _timeouts(self, monkeypatch, error=None):
        """Record (model, prompt) per call; every call goes unanswered."""
        calls = []
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)

        def fake_post(url, **kwargs):
            calls.append((
                url.split("/models/")[1].split(":")[0],
                kwargs["json"]["contents"][0]["parts"][0]["text"],
            ))
            raise error or gemini_tts.requests.ReadTimeout("read timed out")

        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)
        return calls

    def test_timeout_reaches_the_model_change_immediately(self, monkeypatch):
        """The fix: the model change is attempt 2, not the rung the budget
        never affords."""
        calls = self._timeouts(monkeypatch)
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS)
        models = [m for m, _ in calls]
        assert models[0] == gemini_tts.GEMINI_TTS_MODEL
        assert models[1] == gemini_tts.GEMINI_TTS_FALLBACK_MODEL

    def test_timeout_never_sheds_the_prompt(self, monkeypatch):
        """An unanswered request carries no verdict on its own wording."""
        calls = self._timeouts(monkeypatch)
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS, continuing=True)
        # Every call keeps the full-quality shape: the continuation note, the
        # style prompt and the cues all survive.
        assert all(gemini_tts.CONTINUATION_NOTE in prompt for _, prompt in calls)
        style = gemini_tts._style_prompt()
        assert style and all(style in prompt for _, prompt in calls)

    def test_pinned_model_is_asked_again_rather_than_abandoned(self, monkeypatch):
        """With one model left, re-ask it — do not hand the section over early.

        The 2026-08-13 probe measured ~53% success per call on both models, so
        a timeout means flaky, not dead, and a second identical ask is close to
        a coin flip. Only the budget should end this.
        """
        calls = self._timeouts(monkeypatch)
        gemini_tts.set_model_override(gemini_tts.GEMINI_TTS_FALLBACK_MODEL)
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS)
        assert [m for m, _ in calls] == (
            [gemini_tts.GEMINI_TTS_FALLBACK_MODEL] * len(gemini_tts.RETRY_LADDER)
        )

    def test_rejection_still_walks_the_prompt_rungs(self, monkeypatch):
        """finishReason OTHER *is* a verdict on the prompt — keep shedding."""
        seen = []
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)

        def fake_post(*args, **kwargs):
            seen.append(kwargs["json"]["contents"][0]["parts"][0]["text"])
            return TestSynthesizeRetries._FakeResp(
                TestSynthesizeRetries.NO_AUDIO_RESPONSE
            )

        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)
        with pytest.raises(RuntimeError, match="no audio"):
            _synthesize_chunk(SEGS, continuing=True)
        assert len(seen) == len(gemini_tts.RETRY_LADDER)
        assert gemini_tts.CONTINUATION_NOTE in seen[0]
        assert gemini_tts.CONTINUATION_NOTE not in seen[1]  # rung 1 sheds the context

    def test_connection_error_routed_like_a_timeout(self, monkeypatch):
        """A dropped connection is just as silent as a timeout."""
        calls = self._timeouts(
            monkeypatch, error=gemini_tts.requests.ConnectionError("reset by peer")
        )
        with pytest.raises(Exception):
            _synthesize_chunk(SEGS)
        models = [m for m, _ in calls]
        assert models[0] == gemini_tts.GEMINI_TTS_MODEL
        assert models[1] == gemini_tts.GEMINI_TTS_FALLBACK_MODEL

    def test_timeout_then_a_working_model_still_yields_audio(self, monkeypatch):
        """Skipping rungs must not skip the recovery itself."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)

        def fake_post(url, **kwargs):
            if gemini_tts.GEMINI_TTS_MODEL in url:
                raise gemini_tts.requests.ReadTimeout("read timed out")
            return TestSynthesizeRetries._FakeResp(
                TestSynthesizeRetries.AUDIO_RESPONSE
            )

        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)
        pcm, rate = _synthesize_chunk(SEGS)
        assert pcm == TestSynthesizeRetries.AUDIO_PCM
        assert any("retry" in d for d in gemini_tts.drain_degradations())


class TestModelSelection:
    def test_primary_model_used_by_default(self):
        assert gemini_tts._model_for(gemini_tts.RETRY_LADDER[0]) == gemini_tts.GEMINI_TTS_MODEL

    def test_fallback_rung_uses_the_fallback_model(self):
        rung = next(r for r in gemini_tts.RETRY_LADDER if r.fallback_model)
        assert gemini_tts._model_for(rung) == gemini_tts.GEMINI_TTS_FALLBACK_MODEL

    def test_fallback_model_differs_from_primary(self):
        """Falling to the same model would just be a slower retry."""
        assert gemini_tts.GEMINI_TTS_FALLBACK_MODEL != gemini_tts.GEMINI_TTS_MODEL

    def test_override_pins_every_rung(self):
        gemini_tts.set_model_override("pinned-model")
        assert all(
            gemini_tts._model_for(r) == "pinned-model" for r in gemini_tts.RETRY_LADDER
        )

    def test_requested_model_reaches_the_url(self, monkeypatch):
        seen = []
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)

        def fake_post(url, **kwargs):
            seen.append(url)
            return TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.AUDIO_RESPONSE)

        monkeypatch.setattr(gemini_tts.requests, "post", fake_post)
        gemini_tts.set_model_override("some-other-tts")
        _synthesize_chunk(SEGS)
        assert "some-other-tts" in seen[0]


class TestDegradationReporting:
    """A retry that had to shed the style prompt or change model still produced
    the episode — the fallback is usually right, the silence never is."""

    def _patch(self, monkeypatch, responses):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)
        monkeypatch.setattr(gemini_tts.requests, "post", lambda *a, **k: responses.pop(0))

    def test_clean_first_attempt_records_nothing(self, monkeypatch):
        self._patch(monkeypatch, [
            TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.AUDIO_RESPONSE)
        ])
        _synthesize_chunk(SEGS)
        assert gemini_tts.drain_degradations() == []

    def test_a_retry_rung_is_recorded(self, monkeypatch):
        self._patch(monkeypatch, [
            TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.NO_AUDIO_RESPONSE),
            TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.AUDIO_RESPONSE),
        ])
        _synthesize_chunk(SEGS)
        recorded = gemini_tts.drain_degradations()
        assert len(recorded) == 1
        assert "retry 1" in recorded[0]

    def test_drain_clears_the_buffer(self, monkeypatch):
        self._patch(monkeypatch, [
            TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.NO_AUDIO_RESPONSE),
            TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.AUDIO_RESPONSE),
        ])
        _synthesize_chunk(SEGS)
        assert gemini_tts.drain_degradations()
        assert gemini_tts.drain_degradations() == []


class TestCanary:
    """The provider decision is made once, before any audio exists. Three of
    seven episodes in the week of 2026-08-01 shipped with a Gemini cold open and
    an OpenAI show because the fallback fired per-section mid-render."""

    def _patch(self, monkeypatch, responder):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(gemini_tts.time, "sleep", lambda s: None)
        monkeypatch.setattr(gemini_tts.requests, "post", responder)

    def test_returns_primary_model_when_it_answers(self, monkeypatch):
        self._patch(monkeypatch, lambda *a, **k: TestSynthesizeRetries._FakeResp(
            TestSynthesizeRetries.AUDIO_RESPONSE))
        assert gemini_tts.canary() == gemini_tts.GEMINI_TTS_MODEL

    def test_primary_success_leaves_the_ladder_free_to_climb(self, monkeypatch):
        """Pinning the primary here would strand a struggling section on a model
        that is failing it, when a model change keeps the same prebuilt voices."""
        self._patch(monkeypatch, lambda *a, **k: TestSynthesizeRetries._FakeResp(
            TestSynthesizeRetries.AUDIO_RESPONSE))
        gemini_tts.canary()
        assert gemini_tts._model_override is None

    def test_falls_to_the_second_model_and_pins_it(self, monkeypatch):
        """If the primary is down for the run, stop spending attempts on it."""
        seen = []

        def responder(url, **kwargs):
            seen.append(url)
            if gemini_tts.GEMINI_TTS_MODEL in url:
                raise gemini_tts.requests.ConnectionError("flash is down")
            return TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.AUDIO_RESPONSE)

        self._patch(monkeypatch, responder)
        assert gemini_tts.canary() == gemini_tts.GEMINI_TTS_FALLBACK_MODEL
        assert gemini_tts._model_override == gemini_tts.GEMINI_TTS_FALLBACK_MODEL
        # The primary is re-asked once — a dropped connection is a verdict on
        # nothing — and the fallback answers first time.
        assert len(seen) == gemini_tts.CANARY_ATTEMPTS + 1

    def test_returns_none_when_no_model_answers(self, monkeypatch):
        def responder(*args, **kwargs):
            raise gemini_tts.requests.ConnectionError("all down")

        self._patch(monkeypatch, responder)
        assert gemini_tts.canary() is None
        assert gemini_tts._model_override is None

    def test_no_api_key_is_a_failed_canary_not_an_exception(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert gemini_tts.canary() is None

    def test_canary_is_a_probe_per_model_not_a_full_ladder(self, monkeypatch):
        """The probe answers 'is Gemini up', so it must stay cheap — climbing the
        whole ladder twice would cost minutes before a single word is rendered."""
        calls = []

        def responder(*args, **kwargs):
            calls.append(1)
            raise gemini_tts.requests.ConnectionError("down")

        self._patch(monkeypatch, responder)
        gemini_tts.canary()
        assert len(calls) == 2 * gemini_tts.CANARY_ATTEMPTS
        assert gemini_tts.CANARY_ATTEMPTS <= 2  # still a probe, not a ladder

    def test_a_rejected_request_is_not_re_asked(self, monkeypatch):
        """Re-asking is for a request that went unanswered. A model that
        answered with a rejection will reject the identical probe again, and
        every attempt spent here delays the render."""
        calls = []

        def responder(*args, **kwargs):
            calls.append(1)
            return TestSynthesizeRetries._FakeResp({"candidates": [{"finishReason": "OTHER"}]})

        self._patch(monkeypatch, responder)
        assert gemini_tts.canary() is None
        assert len(calls) == 2  # each model asked once, then given up on

    def test_a_read_timeout_is_re_asked_before_the_episode_leaves_gemini(self, monkeypatch):
        """Every canary failure of the week of 2026-08-17 was a read timeout,
        and the 2026-08-13 probe measured 8 of 15 identical calls answering."""
        calls = []

        def responder(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise gemini_tts.requests.Timeout("read timed out")
            return TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.AUDIO_RESPONSE)

        self._patch(monkeypatch, responder)
        assert gemini_tts.canary() == gemini_tts.GEMINI_TTS_MODEL
        assert len(calls) == 2

    def test_canary_uses_a_short_read_timeout(self, monkeypatch):
        seen = []

        def responder(url, **kwargs):
            seen.append(kwargs.get("timeout"))
            return TestSynthesizeRetries._FakeResp(TestSynthesizeRetries.AUDIO_RESPONSE)

        self._patch(monkeypatch, responder)
        gemini_tts.canary()
        assert seen[0] == (gemini_tts.REQUEST_CONNECT_TIMEOUT, gemini_tts.CANARY_READ_TIMEOUT)
        assert gemini_tts.CANARY_READ_TIMEOUT < gemini_tts.REQUEST_READ_TIMEOUT

    def test_canary_text_is_short_enough_to_skip_the_duration_check(self):
        """A tiny probe clip must not be failed by the truncation guard, which
        would report a healthy provider as dead."""
        words = sum(
            len(s["text"].split()) for s in gemini_tts.CANARY_SEGMENTS
        )
        assert words < 10


class TestSectionGeneration:
    def test_writes_wav_and_chunks_long_sections(self, monkeypatch, tmp_path):
        calls = []

        def fake_synthesize(chunk, continuing=False):
            calls.append(chunk)
            return b"\x00\x00" * 2400, 24_000  # 100 ms of silence

        monkeypatch.setattr(gemini_tts, "_synthesize_chunk", fake_synthesize)

        long_text = "word " * 400  # ~2000 chars per turn
        segments = [
            {"speaker": "riley" if i % 2 == 0 else "casey", "text": long_text, "gap_ms": None}
            for i in range(6)
        ]  # ~12k chars total > TRANSCRIPT_CHAR_LIMIT → multiple chunks
        assert sum(len(s["text"]) for s in segments) > TRANSCRIPT_CHAR_LIMIT

        out = tmp_path / "section.wav"
        generate_gemini_tts_for_section(segments, out)

        assert len(calls) > 1
        assert sum(len(c) for c in calls) == len(segments)
        with wave.open(str(out), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 24_000
            assert wav.getnframes() > 0

    def test_continuation_flag_threads_across_chunks_and_sections(self, monkeypatch, tmp_path):
        """The first chunk of the episode's first section starts cold; every
        chunk after it, and every later section, opens mid-flow."""
        received = []

        def fake_synthesize(chunk, continuing=False):
            received.append(continuing)
            return b"\x00\x00" * 2400, 24_000

        monkeypatch.setattr(gemini_tts, "_synthesize_chunk", fake_synthesize)

        long_text = "word " * 400
        segments = [
            {"speaker": "riley" if i % 2 == 0 else "casey", "text": long_text, "gap_ms": None}
            for i in range(6)
        ]

        out = tmp_path / "section.wav"
        returned = generate_gemini_tts_for_section(segments, out)

        assert len(received) > 1
        assert received[0] is False          # episode's first audio, nothing before it
        assert all(received[1:])             # later chunks continue
        # The caller feeds this straight into the next section's call.
        assert returned is True

    def test_first_section_flag_is_honoured(self, monkeypatch, tmp_path):
        received = []

        def fake_synthesize(chunk, continuing=False):
            received.append(continuing)
            return b"\x00\x00" * 2400, 24_000

        monkeypatch.setattr(gemini_tts, "_synthesize_chunk", fake_synthesize)
        segments = [{"speaker": "riley", "text": "Short.", "gap_ms": None}]
        generate_gemini_tts_for_section(segments, tmp_path / "s.wav", continuing=True)
        assert received == [True]


class TestEvaluateScriptLoader:
    def test_prefers_podcast_data_json(self, tmp_path):
        from evaluate_tts import _find_latest_script
        (tmp_path / "podcast_data_2026-07-01.json").write_text('{"script": "from json"}')
        (tmp_path / "podcast_script_2026-07-16_theme.txt").write_text("from txt")
        assert _find_latest_script(tmp_path) == {"script": "from json"}

    def test_falls_back_to_committed_script_txt(self, tmp_path):
        from evaluate_tts import _find_latest_script
        (tmp_path / "podcast_script_2026-07-15_theme.txt").write_text("older")
        (tmp_path / "podcast_script_2026-07-16_theme.txt").write_text("**RILEY:** hi")
        assert _find_latest_script(tmp_path) == {"script": "**RILEY:** hi"}

    def test_none_when_empty(self, tmp_path):
        from evaluate_tts import _find_latest_script
        assert _find_latest_script(tmp_path) is None


class TestStripStageDirections:
    def test_whitelisted_cue_removed(self):
        result = strip_stage_directions("(wry) Sure it will.")
        assert "(wry)" not in result
        assert "Sure it will." in result

    def test_cue_mid_sentence_removed(self):
        result = strip_stage_directions("Fine. (sighs) Let's hear it.")
        assert "(sighs)" not in result
        assert "Fine." in result and "Let's hear it." in result

    def test_case_insensitive(self):
        assert "(Chuckles)" not in strip_stage_directions("(Chuckles) Right.")

    def test_real_parenthetical_dialog_untouched(self):
        text = "The grant (about forty thousand dollars) closed last week."
        assert strip_stage_directions(text) == text


class TestProviderResolution:
    def _fresh(self, monkeypatch, gemini=False, azure=False, used=None, rendered=()):
        import podcast_generator as pg
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", gemini)
        monkeypatch.setattr(pg, "USE_AZURE_TTS", azure)
        monkeypatch.setattr(pg, "_tts_provider_used", used)
        # Module-level list — reset it or renders from earlier tests leak in.
        monkeypatch.setattr(pg, "_tts_providers_rendered", list(rendered))
        return pg

    def test_default_is_openai(self, monkeypatch):
        pg = self._fresh(monkeypatch)
        assert pg.get_active_tts_provider() == "openai"
        assert "OpenAI" in pg.get_tts_credit()

    def test_azure_flag(self, monkeypatch):
        pg = self._fresh(monkeypatch, azure=True)
        assert pg.get_active_tts_provider() == "azure"
        assert "Azure" in pg.get_tts_credit()

    def test_gemini_flag_wins_over_azure(self, monkeypatch):
        pg = self._fresh(monkeypatch, gemini=True, azure=True)
        assert pg.get_active_tts_provider() == "gemini"
        assert pg.get_tts_credit() == "Gemini TTS"

    def test_rendered_provider_beats_flags(self, monkeypatch):
        # Gemini requested, but the run fell back to OpenAI — credit OpenAI
        pg = self._fresh(monkeypatch, gemini=True, used="openai")
        assert pg.get_active_tts_provider() == "openai"
        assert "OpenAI" in pg.get_tts_credit()

    def test_plain_text_credits_reflect_provider(self, monkeypatch):
        pg = self._fresh(monkeypatch, gemini=True)
        text = render_credits_text(pg.get_tts_credit())
        assert "Today's Voices: Gemini TTS" in text
        assert "{tts_credit}" not in text

    def test_citations_credit_refreshed_after_fallback(self, monkeypatch, tmp_path):
        # Citations were written while Gemini was the flagged provider, then
        # rendering fell back to OpenAI — the file must be re-credited.
        import json
        pg = self._fresh(monkeypatch, gemini=True, used="openai")
        citations = tmp_path / "citations_2026-07-23_test_theme.json"
        citations.write_text(json.dumps(
            {"credits": {"text_to_speech": "Gemini TTS"}}
        ), encoding="utf-8")
        pg.refresh_citations_tts_credit(citations)
        data = json.loads(citations.read_text(encoding="utf-8"))
        assert "OpenAI" in data["credits"]["text_to_speech"]

    def test_citations_refresh_missing_file_is_noop(self, monkeypatch, tmp_path):
        pg = self._fresh(monkeypatch, gemini=True, used="openai")
        pg.refresh_citations_tts_credit(tmp_path / "nope.json")  # must not raise

    def test_rendered_providers_beat_the_routing_pin(self, monkeypatch):
        # Gemini rendered the cold open before the 429, OpenAI rendered the rest
        # (2026-07-26). The audio is genuinely mixed — credit both.
        pg = self._fresh(monkeypatch, gemini=True, used="openai",
                         rendered=("gemini", "openai"))
        assert pg.get_tts_credit() == "Gemini TTS and OpenAI TTS"
        # The routing pin still routes remaining work to OpenAI.
        assert pg.get_active_tts_provider() == "openai"

    def test_single_rendered_provider_is_named_alone(self, monkeypatch):
        pg = self._fresh(monkeypatch, gemini=True, rendered=("gemini",))
        assert pg.get_tts_credit() == "Gemini TTS"

    def test_script_stage_falls_back_to_requested_provider(self, monkeypatch):
        # Nothing has rendered yet — the requested provider is the best guess.
        pg = self._fresh(monkeypatch, gemini=True)
        assert pg.get_tts_credit() == "Gemini TTS"

    def test_record_tts_render_dedupes_and_keeps_order(self, monkeypatch):
        pg = self._fresh(monkeypatch, gemini=True)
        for provider in ("gemini", "openai", "gemini", "openai"):
            pg.record_tts_render(provider)
        assert pg._tts_providers_rendered == ["gemini", "openai"]

    def test_three_providers_use_serial_comma(self, monkeypatch):
        pg = self._fresh(monkeypatch, rendered=("gemini", "azure", "openai"))
        assert pg.get_tts_credit() == "Gemini TTS, Azure Neural TTS and OpenAI TTS"

    def test_refresh_repairs_description_not_just_credits_key(self, monkeypatch, tmp_path):
        # The 2026-07-26 defect: refresh fixed credits.text_to_speech and left
        # the description saying Gemini — and the RSS publishes the description.
        import json
        pg = self._fresh(monkeypatch, gemini=True, used="openai", rendered=("openai",))
        citations = tmp_path / "citations_2026-07-26_test_theme.json"
        citations.write_text(json.dumps({
            "credits": {"text_to_speech": "Gemini TTS"},
            "episode": {"description": "<p>Notes</p><p><b>Credits</b><br>"
                                       "Today's Voices: Gemini TTS<br>"
                                       "Cover Art: someone<br></p>"},
        }), encoding="utf-8")
        pg.refresh_citations_tts_credit(citations)
        data = json.loads(citations.read_text(encoding="utf-8"))
        assert data["credits"]["text_to_speech"] == "OpenAI TTS"
        assert "Today's Voices: OpenAI TTS" in data["episode"]["description"]
        assert "Gemini" not in data["episode"]["description"]

    def test_refresh_repairs_description_when_credits_key_already_correct(
            self, monkeypatch, tmp_path):
        # Regression: the old early-return bailed as soon as the credits key
        # matched, which is exactly when the description was left stale.
        import json
        pg = self._fresh(monkeypatch, gemini=True, used="openai", rendered=("openai",))
        citations = tmp_path / "citations_2026-07-26_test_theme.json"
        citations.write_text(json.dumps({
            "credits": {"text_to_speech": "OpenAI TTS"},
            "episode": {"description": "Today's Voices: Gemini TTS<br>"},
        }), encoding="utf-8")
        pg.refresh_citations_tts_credit(citations)
        data = json.loads(citations.read_text(encoding="utf-8"))
        assert "Today's Voices: OpenAI TTS" in data["episode"]["description"]

    def test_refresh_leaves_a_consistent_file_untouched(self, monkeypatch, tmp_path):
        import json
        pg = self._fresh(monkeypatch, gemini=True, rendered=("gemini",))
        citations = tmp_path / "citations_2026-07-26_test_theme.json"
        original = json.dumps({
            "credits": {"text_to_speech": "Gemini TTS"},
            "episode": {"description": "Today's Voices: Gemini TTS<br>"},
        })
        citations.write_text(original, encoding="utf-8")
        pg.refresh_citations_tts_credit(citations)
        assert citations.read_text(encoding="utf-8") == original


class TestStageDirectionAddendum:
    def test_disabled_without_gemini(self, monkeypatch):
        import podcast_generator as pg
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", False)
        assert pg._stage_direction_addendum() == ""

    def test_enabled_with_gemini_lists_cues(self, monkeypatch):
        import podcast_generator as pg
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", True)
        addendum = pg._stage_direction_addendum()
        assert "STAGE DIRECTIONS" in addendum
        assert "(wry)" in addendum
        assert "{cue_list}" not in addendum
