"""Shared fixtures and import-time mocks for tests.

podcast_generator.py calls sys.exit(1) if anthropic/openai/pydub are missing,
which happens during pytest collection before any fixtures can run. We need to
install stub modules before any test file imports podcast_generator.
"""

import sys
import types


def _install_stubs():
    """Install lightweight stubs for heavy third-party packages."""
    for mod_name in ("anthropic", "openai", "pydub", "cohere"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    sys.modules["anthropic"].Anthropic = type("Anthropic", (), {})
    sys.modules["openai"].OpenAI = type("OpenAI", (), {})
    sys.modules["pydub"].AudioSegment = type("AudioSegment", (), {
        "from_mp3": staticmethod(lambda *a, **k: None),
        "silent": staticmethod(lambda *a, **k: None),
        "empty": staticmethod(lambda *a, **k: None),
    })

    # Azure Speech SDK stub — azure_tts.py imports this at call time (inside functions),
    # but providing a stub lets any top-level import in test files succeed cleanly.
    for mod_name in ("azure", "azure.cognitiveservices", "azure.cognitiveservices.speech"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    # Google API client stubs — youtube_upload.py imports these inside functions,
    # so tests exercise metadata/ledger logic without network or credentials.
    for mod_name in (
        "googleapiclient", "googleapiclient.discovery", "googleapiclient.http",
        "google", "google.oauth2", "google.oauth2.credentials",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)
    sys.modules["googleapiclient.discovery"].build = lambda *a, **k: None
    sys.modules["googleapiclient.http"].MediaFileUpload = type("MediaFileUpload", (), {
        "__init__": lambda self, *a, **k: None,
    })
    sys.modules["google.oauth2.credentials"].Credentials = type("Credentials", (), {
        "__init__": lambda self, *a, **k: None,
    })


# Run at import time so stubs are ready before test modules are collected
_install_stubs()


import shutil

import pytest


@pytest.fixture(autouse=True)
def _isolate_psa_state(tmp_path, monkeypatch):
    """Redirect PSA rotation state to a tmp copy for every test.

    select_psa() persists round-robin state on each call; without this, a local
    test run silently rewrites podcasts/psa_rotation_state.json — live rotation
    state that CI commits daily (tripped us twice: dirty working trees and a
    near-miss committing rolled-back org dates).
    """
    import psa_selector

    tmp_state = tmp_path / "psa_rotation_state.json"
    if psa_selector.PSA_STATE_FILE.exists():
        shutil.copy(psa_selector.PSA_STATE_FILE, tmp_state)
    monkeypatch.setattr(psa_selector, "PSA_STATE_FILE", tmp_state)


@pytest.fixture(autouse=True)
def _isolate_anchor_state(tmp_path, monkeypatch):
    """Redirect weekly anchor state to an *empty* tmp file for every test.

    select_anchor() pins the week's question and appends to the no-repeat ledger
    on the first call of a week, so any test that reaches it would otherwise
    rewrite podcasts/weekly_anchor_state.json — live state CI commits daily.
    Same hazard as _isolate_psa_state above; deliberately not the same fix.

    PSA state is copied because round-robin only means anything against real
    rotation history. Anchor state is the opposite: every entry in it *removes*
    a question from the pool forever, so copying it makes the tests weaker every
    week the show airs, and a test that walks the seeded pool end to end fails
    the moment production has spent one. It failed exactly that way on
    2026-08-12. Starting empty is what makes these assertions about the config.

    Draining degradations keeps weekly_anchor's module-level ledger — the
    workaround for its circular-import problem — from leaking between tests.
    """
    import weekly_anchor

    monkeypatch.setattr(weekly_anchor, "PODCASTS_DIR", tmp_path)
    monkeypatch.setattr(weekly_anchor, "ANCHOR_STATE_FILE",
                        tmp_path / "weekly_anchor_state.json")
    weekly_anchor.drain_degradations()


@pytest.fixture(autouse=True)
def _isolate_phrase_ledger(tmp_path, monkeypatch):
    """Redirect the phrase ledger to an *empty* tmp file for every test.

    update_phrase_ledger() writes on every call, and podcasts/phrase_ledger.json
    is live state CI commits daily — same hazard as _isolate_psa_state.

    Empty rather than copied, for the anchor's reason: the burned list is derived
    from whatever episodes are in the window, so a copied ledger would make every
    assertion about production's last three weeks instead of about the fixture.
    """
    import podcast_generator

    monkeypatch.setattr(podcast_generator, "PHRASE_LEDGER_FILE",
                        tmp_path / "phrase_ledger.json")
