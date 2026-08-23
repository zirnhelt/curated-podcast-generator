"""Tests for the AI-tell corpus, the phrase ledger and the scrub gate.

The mechanism these cover exists because prose bans in the prompt did not work:
config/prompts.json had banned "[X] is carrying a lot of weight in that sentence"
verbatim for a long time and the phrase still shipped, while "genuinely" reached
146 uses across 30 episodes without any single episode looking unusual.
"""

import json

import pytest

import config_loader
import podcast_generator as pg


def _script(*turns):
    """Minimal script in the real on-air format."""
    body = "\n\n".join(f"**{who}:** {text}" for who, text in turns)
    return "# Cariboo Signals Podcast Script - 2026-08-18\n\n**NEWS ROUNDUP**\n\n" + body


class TestConfigLoading:
    def test_ai_tells_config_loads(self):
        cfg = config_loader.load_ai_tells_config()
        assert cfg["hard_banned"], "hard_banned must not be empty"
        assert cfg["patterns"], "pattern families must be present"

    def test_ai_tells_config_optional(self, tmp_path, monkeypatch):
        """A missing style file costs enforcement, never a run."""
        monkeypatch.setattr(config_loader, "CONFIG_DIR", tmp_path)
        config_loader.load_ai_tells_config.cache_clear()
        try:
            assert config_loader.load_ai_tells_config() == {}
        finally:
            config_loader.load_ai_tells_config.cache_clear()

    def test_every_pattern_compiles(self):
        import re

        cfg = config_loader.load_ai_tells_config()
        for family, pats in cfg["patterns"].items():
            for pat in pats:
                re.compile(pat)
        for family, spec in cfg["soft_patterns"].items():
            for pat in spec["patterns"]:
                re.compile(pat)
        for pat in cfg["rhythm"]["antithesis_patterns"]:
            re.compile(pat)

    def test_user_named_phrases_are_banned(self):
        """The three phrases that prompted this mechanism."""
        banned = " ".join(config_loader.load_ai_tells_config()["hard_banned"]).lower()
        assert "genuinely" in banned
        assert "carrying a lot of weight" in banned
        assert "not nothing" in banned


class TestPromptsDoNotTeachTheTic:
    """The prompt's own register leaks into the output.

    "genuinely" appeared 44 times in prompts.json prose and 146 times across 30
    scripts; "directly" 31 and 94. The model was echoing its instructions, which
    no ban list catches — the ban and the example were in the same file.
    """

    @staticmethod
    def _prose(template_text):
        import re

        # Quoted spans are the ban list quoting the phrase it forbids, plus the
        # rhythm section's examples. Those must survive; prose use must not.
        return re.sub(r'"[^"]*"', " ", template_text)

    def test_no_hard_banned_phrase_in_prompt_prose(self):
        import re

        prompts = config_loader.load_prompts_config()
        banned = config_loader.load_ai_tells_config()["hard_banned"]
        pattern = re.compile(
            "|".join(r"\b" + re.escape(p) + r"\b" for p in banned), re.IGNORECASE
        )
        offenders = []
        for key, entry in prompts.items():
            if not isinstance(entry, dict):
                continue
            for field, text in entry.items():
                if not isinstance(text, str):
                    continue
                for match in pattern.finditer(self._prose(text)):
                    offenders.append(f"{key}.{field}: {match.group(0)!r}")
        assert not offenders, (
            "prompt prose uses a phrase it bans — the model copies its "
            "instructions' register:\n  " + "\n  ".join(offenders[:10])
        )

    def test_bespoke_inherits_the_bans(self):
        """generate_bespoke builds its own prompt and used to inherit nothing."""
        import generate_bespoke

        assert "BURNED PHRASES" in generate_bespoke.SYSTEM_PROMPT

    def test_static_block_shared_with_bespoke(self):
        block = config_loader.format_static_tell_block()
        assert "genuinely" in block
        assert "RHYTHM BUDGET" in block


class TestExtractPhraseCounts:
    def test_ignores_speaker_and_pacing_tags(self):
        counts = pg.extract_phrase_counts(
            _script(("RILEY", "[pause:1200] The report landed quietly."))
        )
        assert "quietly" in counts
        assert not any("riley" in k or "pause" in k for k in counts)

    def test_proper_nouns_never_counted(self):
        """Place names are the subject matter, not a tic."""
        text = _script(("RILEY", "Williams Lake and Quesnel and the Cariboo Regional District."))
        counts = pg.extract_phrase_counts(text)
        assert not any(w in counts for w in ("williams", "quesnel", "cariboo", "district"))

    def test_content_words_not_counted(self):
        """A news show says "story" and "region" constantly and must keep doing so."""
        counts = pg.extract_phrase_counts(
            _script(("CASEY", "the story about the region raises a question about the story"))
        )
        assert "story" not in counts and "region" not in counts and "question" not in counts

    def test_adverbs_are_counted(self):
        counts = pg.extract_phrase_counts(
            _script(("RILEY", "It landed quietly. It landed quietly again."))
        )
        assert counts.get("quietly") == 2

    def test_non_adverb_ly_words_excluded(self):
        counts = pg.extract_phrase_counts(
            _script(("CASEY", "The supply chain and the family farm and the supply again."))
        )
        assert "supply" not in counts and "family" not in counts


class TestPhraseLedger:
    def _episodes(self, n, phrase, per_episode, filler_words=900):
        """n episodes each using *phrase* per_episode times."""
        ledger = None
        for i in range(n):
            body = " ".join([f"It landed {phrase}."] * per_episode)
            body += " " + " ".join(["padding"] * filler_words)
            ledger = pg.update_phrase_ledger(_script(("RILEY", body)), f"2026-01-{i + 1:02d}")
        return ledger

    def test_promotes_a_repeated_adverb(self):
        ledger = self._episodes(8, "quietly", 6)
        assert "quietly" in ledger["burned"]
        assert ledger["burned"]["quietly"]["count"] == 48

    def test_boilerplate_never_burned(self):
        """Said once per episode, every episode — that is a fixture, not a tic."""
        ledger = self._episodes(8, "quietly", 1)
        assert "quietly" not in ledger["burned"]

    def test_window_is_pruned(self):
        window = pg._ledger_settings()["window_episodes"]
        ledger = self._episodes(window + 5, "quietly", 6)
        assert len(ledger["episodes"]) == window

    def test_idempotent_on_rerun(self):
        """A re-render must not double-count its own episode into the rates."""
        script = _script(("RILEY", "It landed quietly. " * 6))
        first = pg.update_phrase_ledger(script, "2026-02-01")
        second = pg.update_phrase_ledger(script, "2026-02-01")
        assert len(first["episodes"]) == len(second["episodes"]) == 1
        assert first["episodes"][0]["counts"] == second["episodes"][0]["counts"]

    def test_retires_after_clean_episodes(self):
        ledger = self._episodes(8, "quietly", 6)
        assert "quietly" in ledger["burned"]
        retire = pg._ledger_settings()["retire_after_clean_episodes"]
        for i in range(retire):
            ledger = pg.update_phrase_ledger(
                _script(("RILEY", "Nothing to report here at all.")), f"2026-03-{i + 1:02d}"
            )
        assert "quietly" not in ledger["burned"], "a phrase that goes quiet must free its slot"

    def test_prompt_block_empty_ledger_still_has_hard_bans(self):
        block = pg.format_burned_phrases_for_prompt({"burned": {}, "episodes": []})
        assert "genuinely" in block
        assert "MEASURED OVERUSE" not in block

    def test_prompt_block_reports_measured_phrases(self):
        ledger = self._episodes(8, "quietly", 6)
        block = pg.format_burned_phrases_for_prompt(ledger)
        assert "MEASURED OVERUSE" in block
        assert "quietly" in block

    def test_hard_banned_not_duplicated_in_measured_line(self):
        ledger = self._episodes(8, "genuinely", 6)
        block = pg.format_burned_phrases_for_prompt(ledger)
        measured = [ln for ln in block.split("\n") if ln.startswith("MEASURED OVERUSE")]
        assert not measured or "genuinely" not in measured[0]


class TestHardBannedGate:
    def test_finds_each_banned_phrase(self):
        script = _script(
            ("RILEY", "That is a genuinely surprising number."),
            ("CASEY", "Fifty jobs is not nothing."),
        )
        found = {phrase for phrase, _ in pg.find_hard_banned(script)}
        assert "genuinely" in found and "not nothing" in found

    def test_returns_the_containing_sentence_without_tags(self):
        script = _script(("RILEY", "[pause:1200] That is a genuinely surprising number."))
        (_, sentence), = pg.find_hard_banned(script)
        assert sentence == "That is a genuinely surprising number."
        assert "**RILEY:**" not in sentence and "[pause" not in sentence

    def test_clean_script_has_no_hits(self):
        assert pg.find_hard_banned(_script(("CASEY", "Fifty jobs went with it."))) == []

    def test_scrub_without_client_degrades_rather_than_dropping_silently(self, monkeypatch):
        """A silent fallback is the failure mode degrade() exists to prevent."""
        recorded = []
        monkeypatch.setattr(pg, "get_anthropic_client", lambda: None)
        monkeypatch.setattr(pg, "degrade", lambda name, detail: recorded.append(name))
        script = _script(("RILEY", "That is a genuinely surprising number."))
        assert pg.scrub_hard_banned(script, pg.find_hard_banned(script)) == script
        assert recorded == ["script/tell-scrub"]

    def test_scrub_keeps_original_when_rewrite_still_contains_the_phrase(self, monkeypatch):
        """A bad rewrite must never make the episode worse than the tic."""
        recorded = []
        monkeypatch.setattr(pg, "degrade", lambda name, detail: recorded.append(detail))
        monkeypatch.setattr(pg, "get_anthropic_client", lambda: object())
        monkeypatch.setattr(pg, "api_retry", lambda fn, **kw: None)
        monkeypatch.setattr(pg, "_log_api_call", lambda *a, **k: None)
        monkeypatch.setattr(
            pg, "message_text",
            lambda r: json.dumps(["That is a genuinely surprising figure."]),
        )
        script = _script(("RILEY", "That is a genuinely surprising number."))
        assert pg.scrub_hard_banned(script, pg.find_hard_banned(script)) == script
        assert recorded and "rewrite rejected" in recorded[0]

    def test_scrub_splices_a_clean_rewrite(self, monkeypatch):
        monkeypatch.setattr(pg, "get_anthropic_client", lambda: object())
        monkeypatch.setattr(pg, "api_retry", lambda fn, **kw: None)
        monkeypatch.setattr(pg, "_log_api_call", lambda *a, **k: None)
        monkeypatch.setattr(
            pg, "message_text", lambda r: json.dumps(["That is a surprising number."])
        )
        script = _script(("RILEY", "That is a genuinely surprising number."))
        out = pg.scrub_hard_banned(script, pg.find_hard_banned(script))
        assert "genuinely" not in out
        assert "That is a surprising number." in out
        assert "**RILEY:**" in out

    def test_scrub_degrades_on_malformed_response(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(pg, "degrade", lambda name, detail: recorded.append(detail))
        monkeypatch.setattr(pg, "get_anthropic_client", lambda: object())
        monkeypatch.setattr(pg, "api_retry", lambda fn, **kw: None)
        monkeypatch.setattr(pg, "_log_api_call", lambda *a, **k: None)
        monkeypatch.setattr(pg, "message_text", lambda r: "not json at all")
        script = _script(("RILEY", "That is a genuinely surprising number."))
        assert pg.scrub_hard_banned(script, pg.find_hard_banned(script)) == script
        assert recorded


class TestRhythmScoring:
    def test_counts_short_turns_and_em_dashes(self):
        script = _script(
            ("RILEY", "When?"),
            ("CASEY", "Tuesday."),
            ("RILEY", "The fire took a hundred and fifty structures — every one of them "
             "insured, every one of them a claim that somebody has to process before winter."),
        )
        r = pg.score_rhythm(script)
        assert r["short_turns"] == 2
        assert r["turns"] == 3
        assert r["em_dashes_per_1k"] > 0

    def test_flags_over_budget_em_dashes(self):
        script = _script(("RILEY", "one — two — three — four — five — six"))
        assert "em_dashes" in pg.score_rhythm(script)["over_budget"]

    def test_counts_antithesis(self):
        script = _script(("CASEY", "It isn't nostalgia, it's function."))
        assert pg.score_rhythm(script)["antithesis_hits"] == 1

    def test_score_script_embeds_rhythm(self):
        q = pg.score_script(_script(("RILEY", "When?")))
        assert "rhythm" in q and "em_dashes_per_1k" in q["rhythm"]


class TestScoreScriptConfigWiring:
    def test_patterns_come_from_config(self, monkeypatch):
        monkeypatch.setitem(
            pg.CONFIG, "ai_tells",
            {"patterns": {"custom": [r"\bfrobnicate\b"]}, "hard_banned": []},
        )
        q = pg.score_script(_script(("RILEY", "We frobnicate the thing.")))
        assert q["pattern_hits"]["custom"] == 1
        assert q["total_hits"] == 1

    def test_falls_back_when_config_absent(self, monkeypatch):
        """A style file must never be able to fail a run."""
        monkeypatch.setitem(pg.CONFIG, "ai_tells", {})
        q = pg.score_script(_script(("RILEY", "Let's unpack this.")))
        assert q["pattern_hits"]["pedagogical_voice"] == 1

    def test_config_and_fallback_stay_equivalent(self):
        """Drift between two copies of one list is how four copies of the voice
        audit ended up disagreeing. Compared by behaviour, not by source text."""
        import re

        cfg = config_loader.load_ai_tells_config()
        probe = " ".join([
            "Here's the thing. Let's unpack this. It's worth noting that experts say so.",
            "I want to flag that we utilize a robust ecosystem. In conclusion, that's a fair point.",
            "It serves as a perfect storm. Think of it as worth watching. That's the story.",
        ])
        for name, fallback in (("patterns", pg._FALLBACK_TELL_PATTERNS),
                               ("soft", pg._FALLBACK_SOFT_PATTERNS)):
            live = cfg["patterns"] if name == "patterns" else cfg["soft_patterns"]
            assert set(fallback) <= set(live), f"{name}: family dropped from config"
            for family in fallback:
                fb = fallback[family] if name == "patterns" else fallback[family]["patterns"]
                lv = live[family] if name == "patterns" else live[family]["patterns"]
                count = lambda pats: sum(len(re.findall(p, probe, re.I)) for p in pats)
                assert count(fb) == count(lv), f"{family} behaves differently in config"

    def test_hard_banned_excluded_from_total_hits(self):
        """The scrub gate runs after scoring; counting them would double-charge."""
        q = pg.score_script(_script(("RILEY", "That is a genuinely surprising number.")))
        assert q["pattern_hits"]["hard_banned"] == 1
        assert q["total_hits"] == 0
