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
