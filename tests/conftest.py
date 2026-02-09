"""Shared fixtures and import-time mocks for tests.

podcast_generator.py calls sys.exit(1) if anthropic/openai/pydub are missing,
which happens during pytest collection before any fixtures can run. We need to
install stub modules before any test file imports podcast_generator.
"""

import sys
import types


def _install_stubs():
    """Install lightweight stubs for heavy third-party packages."""
    for mod_name in ("anthropic", "openai", "pydub"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    sys.modules["anthropic"].Anthropic = type("Anthropic", (), {})
    sys.modules["openai"].OpenAI = type("OpenAI", (), {})
    sys.modules["pydub"].AudioSegment = type("AudioSegment", (), {
        "from_mp3": staticmethod(lambda *a, **k: None),
        "silent": staticmethod(lambda *a, **k: None),
        "empty": staticmethod(lambda *a, **k: None),
    })


# Run at import time so stubs are ready before test modules are collected
_install_stubs()
