"""Generate themed intermission chimes for Cariboo Signals.

Creates 7 MP3 files — one per podcast theme — by slicing and processing
cariboo-signals-intro.mp3.  Each variant is a recognisable echo of the
theme song, pitched and EQ'd to match its weekly theme character.

No abstract synthesis: every chime is built from the actual theme song so
they all feel sonically related to the show's on-air identity.

Usage:
    python generate_ambient_chimes.py

Output:
    ambient/ambient-arts.mp3
    ambient/ambient-industry.mp3
    ambient/ambient-civic.mp3
    ambient/ambient-indigenous.mp3
    ambient/ambient-wilderness.mp3
    ambient/ambient-community.mp3
    ambient/ambient-futures.mp3
"""

import io
import os
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt, resample_poly

SAMPLE_RATE = 44100
TARGET_DURATION_S = 5.0
AMBIENT_DIR = Path(__file__).parent / "ambient"
THEME_SONG = Path(__file__).parent / "cariboo-signals-intro.mp3"
FULL_SONG = Path(__file__).parent / "cariboo-signals-full.mp3"

# ── ffmpeg helpers ─────────────────────────────────────────────────────────────

def _ffmpeg_exe() -> str:
    """Return path to ffmpeg; try system PATH first, then imageio_ffmpeg."""
    import shutil
    system_ff = shutil.which("ffmpeg")
    if system_ff:
        return system_ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("ffmpeg not found; install it or pip install imageio-ffmpeg")


def _load_mp3(path: Path) -> np.ndarray:
    """Decode an MP3 to a (2, N) float32 stereo array at SAMPLE_RATE."""
    ff = _ffmpeg_exe()
    result = subprocess.run(
        [ff, "-i", str(path),
         "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "2",
         "-loglevel", "error", "pipe:1"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {path}: {result.stderr.decode()}")
    raw = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    # interleaved stereo → (2, N)
    return raw.reshape(-1, 2).T


def _export_mp3(stereo: np.ndarray, dest: Path, target_dbfs: float = -20.0) -> None:
    """Normalise a (2, N) float32 array and write to dest as 128 kbps MP3."""
    peak = np.max(np.abs(stereo))
    if peak > 0:
        gain = 10 ** (target_dbfs / 20.0) / peak
        stereo = np.clip(stereo * gain, -1.0, 1.0)

    # Interleave channels → int16
    interleaved = stereo.T.flatten()
    int16 = (interleaved * 32767).astype(np.int16)

    # Write a temporary WAV to stdout, pipe into ffmpeg for MP3 encode
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16.tobytes())
    wav_bytes = buf.getvalue()

    ff = _ffmpeg_exe()
    result = subprocess.run(
        [ff, "-y", "-f", "wav", "-i", "pipe:0",
         "-b:a", "128k", "-loglevel", "error", str(dest)],
        input=wav_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg export failed: {result.stderr.decode()}")

    rms = np.sqrt(np.mean(stereo ** 2))
    import math
    actual_dbfs = 20 * math.log10(rms) if rms > 0 else -100
    dur = stereo.shape[1] / SAMPLE_RATE
    print(f"  ✓ {dest.name}  ({dur:.1f}s, {actual_dbfs:.1f} dBFS)")


# ── audio processing ───────────────────────────────────────────────────────────

def _speed_shift(stereo: np.ndarray, semitones: float) -> np.ndarray:
    """Pitch-shift by resampling (changes tempo by the same factor).

    Positive semitones → higher pitch / shorter duration.
    Negative semitones → lower pitch / longer duration.
    """
    factor = 2.0 ** (semitones / 12.0)
    frac = Fraction(factor).limit_denominator(128)
    up, down = frac.numerator, frac.denominator
    out = np.stack([
        resample_poly(ch, up, down).astype(np.float32)
        for ch in stereo
    ])
    return out


def _butter_filter(stereo: np.ndarray, kind: str, cutoff_hz, order: int = 4) -> np.ndarray:
    """Apply a Butterworth low-pass or high-pass filter."""
    nyq = SAMPLE_RATE / 2.0
    if isinstance(cutoff_hz, (list, tuple)):
        wn = [c / nyq for c in cutoff_hz]
    else:
        wn = cutoff_hz / nyq
    sos = butter(order, wn, btype=kind, output="sos")
    return np.stack([sosfilt(sos, ch).astype(np.float32) for ch in stereo])


def _eq(stereo: np.ndarray,
        low_shelf_db: float = 0.0, low_shelf_hz: float = 250.0,
        high_shelf_db: float = 0.0, high_shelf_hz: float = 4000.0) -> np.ndarray:
    """Very simple 2-band shelf EQ using additive filtered signals."""
    result = stereo.copy()

    if low_shelf_db != 0.0:
        low = _butter_filter(stereo, "low", low_shelf_hz)
        gain = 10 ** (low_shelf_db / 20.0) - 1.0
        result = result + low * gain

    if high_shelf_db != 0.0:
        high = _butter_filter(stereo, "high", high_shelf_hz)
        gain = 10 ** (high_shelf_db / 20.0) - 1.0
        result = result + high * gain

    return result


def _fade(stereo: np.ndarray, fade_in_s: float = 0.08, fade_out_s: float = 0.25) -> np.ndarray:
    """Apply linear fade-in and fade-out."""
    n = stereo.shape[1]
    env = np.ones(n, dtype=np.float32)
    fi = int(SAMPLE_RATE * fade_in_s)
    fo = int(SAMPLE_RATE * fade_out_s)
    if fi > 0:
        env[:fi] = np.linspace(0.0, 1.0, fi)
    if fo > 0:
        env[-fo:] = np.linspace(1.0, 0.0, fo)
    return stereo * env


def _slice(stereo: np.ndarray, start_s: float, duration_s: float) -> np.ndarray:
    """Return a [start, start+duration] slice (zero-padded if needed)."""
    start = int(SAMPLE_RATE * start_s)
    length = int(SAMPLE_RATE * duration_s)
    n = stereo.shape[1]
    clip = stereo[:, start:start + length]
    if clip.shape[1] < length:
        pad = np.zeros((2, length - clip.shape[1]), dtype=np.float32)
        clip = np.concatenate([clip, pad], axis=1)
    return clip


def _trim_to(stereo: np.ndarray, duration_s: float) -> np.ndarray:
    """Trim or zero-pad to exactly duration_s seconds."""
    target = int(SAMPLE_RATE * duration_s)
    n = stereo.shape[1]
    if n >= target:
        return stereo[:, :target]
    pad = np.zeros((2, target - n), dtype=np.float32)
    return np.concatenate([stereo, pad], axis=1)


# ── per-theme variants ─────────────────────────────────────────────────────────
#
# Each generator receives the full decoded intro array and returns a
# TARGET_DURATION_S stereo float32 array ready for export.
#
# Strategy: take overlapping slices from different parts of the theme song
# so each chime starts on distinct musical material, then apply mild
# pitch-shifts and EQ to reinforce the weekly theme's character.
# All variants are unmistakably the same song — just heard through a
# different lens.

def gen_arts(song: np.ndarray) -> np.ndarray:
    """Arts, Culture & Digital Storytelling
    Bright, uplifting — +3 semitones, airy high-end shimmer.
    Opening bars carry the most melodic energy; feels like stepping into
    a creative space.
    """
    # Start from the very opening (0 s); pitch up for brightness
    grab_s = TARGET_DURATION_S * (2.0 ** (3 / 12.0)) + 0.5   # grab extra to fill after resample
    clip = _slice(song, start_s=0.5, duration_s=grab_s)
    shifted = _speed_shift(clip, semitones=+3.0)
    out = _trim_to(shifted, TARGET_DURATION_S)
    out = _eq(out, high_shelf_db=+3.0, high_shelf_hz=5000.0)   # air / sparkle
    return _fade(out)


def gen_industry(song: np.ndarray) -> np.ndarray:
    """Working Lands & Industry
    Deep, grounded — -4 semitones, warm low-end weight.
    Mid-section of the intro has the fuller harmonic content; slowed down
    it feels steady and purposeful, like machinery at work.
    """
    grab_s = TARGET_DURATION_S * (2.0 ** (4 / 12.0)) + 0.5
    clip = _slice(song, start_s=3.0, duration_s=grab_s)
    shifted = _speed_shift(clip, semitones=-4.0)
    out = _trim_to(shifted, TARGET_DURATION_S)
    out = _eq(out, low_shelf_db=+4.0, low_shelf_hz=180.0,      # weight / warmth
                    high_shelf_db=-2.0, high_shelf_hz=6000.0)   # less shrill
    return _fade(out, fade_in_s=0.15)


def gen_civic(song: np.ndarray) -> np.ndarray:
    """Community Tech & Governance
    Clean, clear — 0 semitones, flat EQ, natural presentation.
    The unprocessed theme song is itself professional and civic in character;
    a mid-song slice with no pitch tricks communicates trustworthiness.
    """
    clip = _slice(song, start_s=5.0, duration_s=TARGET_DURATION_S + 0.3)
    out = _trim_to(clip, TARGET_DURATION_S)
    # Slight high-pass to remove any rumble; otherwise untouched
    out = _butter_filter(out, "high", 60.0)
    return _fade(out)


def gen_indigenous(song: np.ndarray) -> np.ndarray:
    """Indigenous Lands & Innovation
    Warm, grounded, natural — -2 semitones, mid warmth.
    A slightly lower pitch settles the song into the landscape; the
    early-mid section has an open, unhurried quality.
    """
    grab_s = TARGET_DURATION_S * (2.0 ** (2 / 12.0)) + 0.5
    clip = _slice(song, start_s=2.0, duration_s=grab_s)
    shifted = _speed_shift(clip, semitones=-2.0)
    out = _trim_to(shifted, TARGET_DURATION_S)
    out = _eq(out, low_shelf_db=+2.5, low_shelf_hz=300.0)      # earthy warmth
    return _fade(out, fade_in_s=0.2)


def gen_wilderness(song: np.ndarray) -> np.ndarray:
    """Wild Spaces & Outdoor Life
    Open, airy — +2 semitones, high-pass for a sense of wide skies.
    Starting further into the song catches a different melodic moment;
    the pitch lift makes it feel light and expansive.
    """
    grab_s = TARGET_DURATION_S * (2.0 ** (2 / 12.0)) + 0.5
    clip = _slice(song, start_s=8.5, duration_s=grab_s)
    shifted = _speed_shift(clip, semitones=+2.0)
    out = _trim_to(shifted, TARGET_DURATION_S)
    out = _eq(out, high_shelf_db=+2.0, high_shelf_hz=4000.0,   # brightness
                    low_shelf_db=-1.5, low_shelf_hz=150.0)      # lift the lows slightly
    return _fade(out)


def gen_community(song: np.ndarray) -> np.ndarray:
    """Cariboo Voices & Local News
    Warm, welcoming — -1 semitone, gentle low-mid boost.
    The theme song already feels friendly; a slight de-tuning and warm
    EQ makes it feel like a familiar morning greeting.
    """
    grab_s = TARGET_DURATION_S * (2.0 ** (1 / 12.0)) + 0.3
    clip = _slice(song, start_s=6.5, duration_s=grab_s)
    shifted = _speed_shift(clip, semitones=-1.0)
    out = _trim_to(shifted, TARGET_DURATION_S)
    out = _eq(out, low_shelf_db=+3.0, low_shelf_hz=250.0)      # cozy warmth
    return _fade(out, fade_in_s=0.12)


def gen_futures(song: np.ndarray) -> np.ndarray:
    """Resilient Rural Futures
    Forward-looking, energised — +5 semitones, bright and crisp.
    The tail end of the intro carries the song's conclusion/climax; pitching
    it up gives it lift and momentum — pointing towards what's next.
    """
    grab_s = TARGET_DURATION_S * (2.0 ** (5 / 12.0)) + 0.5
    clip = _slice(song, start_s=10.5, duration_s=grab_s)
    shifted = _speed_shift(clip, semitones=+5.0)
    out = _trim_to(shifted, TARGET_DURATION_S)
    out = _eq(out, high_shelf_db=+4.0, high_shelf_hz=4500.0)   # crisp & bright
    return _fade(out, fade_in_s=0.06)


# ── main ──────────────────────────────────────────────────────────────────────

THEMES = [
    ("ambient-arts.mp3",       "Arts, Culture & Digital Storytelling", gen_arts),
    ("ambient-industry.mp3",   "Working Lands & Industry",             gen_industry),
    ("ambient-civic.mp3",      "Community Tech & Governance",          gen_civic),
    ("ambient-indigenous.mp3", "Indigenous Lands & Innovation",        gen_indigenous),
    ("ambient-wilderness.mp3", "Wild Spaces & Outdoor Life",           gen_wilderness),
    ("ambient-community.mp3",  "Cariboo Voices & Local News",          gen_community),
    ("ambient-futures.mp3",    "Resilient Rural Futures",              gen_futures),
]


def main():
    AMBIENT_DIR.mkdir(exist_ok=True)

    source = THEME_SONG if THEME_SONG.exists() else FULL_SONG
    if not source.exists():
        raise FileNotFoundError(
            f"Theme song not found at {THEME_SONG} or {FULL_SONG}. "
            "Make sure cariboo-signals-intro.mp3 is in the project root."
        )

    print(f"Loading theme song: {source.name} …")
    song = _load_mp3(source)
    song_dur = song.shape[1] / SAMPLE_RATE
    print(f"  {song_dur:.1f}s, stereo at {SAMPLE_RATE} Hz\n")

    print(f"Generating {len(THEMES)} theme-song variants → {AMBIENT_DIR}/\n")
    for filename, theme, generator in THEMES:
        print(f"[{theme}]")
        out = generator(song)
        _export_mp3(out, AMBIENT_DIR / filename)

    print(f"\nDone. {len(THEMES)} files written to {AMBIENT_DIR}/")


if __name__ == "__main__":
    main()
