"""Generate themed intermission chimes for Cariboo Signals.

Creates 7 synthetic ambient MP3 files — one per podcast theme — using
numpy additive synthesis and pydub for audio processing and export.
Each clip is ~5 seconds, faded, and exported to ambient/ at -20 dBFS
(the ambient.py loader normalises them further to -28 dBFS at runtime).

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
import wave
from pathlib import Path

import numpy as np
from pydub import AudioSegment

SAMPLE_RATE = 44100
DURATION_S = 5.0
AMBIENT_DIR = Path(__file__).parent / "ambient"

# ── synthesis helpers ─────────────────────────────────────────────────────────

def _sine(freq: float, duration_s: float, amplitude: float = 1.0) -> np.ndarray:
    """Return a sine wave as a float64 array in [-1, 1]."""
    t = np.linspace(0, duration_s, int(SAMPLE_RATE * duration_s), endpoint=False)
    return amplitude * np.sin(2.0 * np.pi * freq * t)


def _adsr(
    n_samples: int,
    attack_s: float = 0.4,
    decay_s: float = 0.3,
    sustain: float = 0.7,
    release_s: float = 1.0,
) -> np.ndarray:
    """Return an ADSR amplitude envelope of length n_samples."""
    a = int(SAMPLE_RATE * attack_s)
    d = int(SAMPLE_RATE * decay_s)
    r = int(SAMPLE_RATE * release_s)
    # Clamp so segments never exceed total length
    total = n_samples
    a = min(a, total // 4)
    d = min(d, total // 4)
    r = min(r, total // 4)
    s_len = total - a - d - r

    env = np.zeros(n_samples)
    env[:a] = np.linspace(0.0, 1.0, a)
    env[a : a + d] = np.linspace(1.0, sustain, d)
    if s_len > 0:
        env[a + d : a + d + s_len] = sustain
    env[a + d + s_len :] = np.linspace(sustain, 0.0, r)
    return env


def _exp_decay(n_samples: int, tau_s: float = 1.5) -> np.ndarray:
    """Return an exponential-decay amplitude envelope."""
    t = np.linspace(0, n_samples / SAMPLE_RATE, n_samples)
    env = np.exp(-t / tau_s)
    return env


def _chord(freqs_amps: list, duration_s: float) -> np.ndarray:
    """Mix sine tones; return normalised float64 array."""
    n = int(SAMPLE_RATE * duration_s)
    out = np.zeros(n)
    for freq, amp in freqs_amps:
        out += _sine(freq, duration_s, amp)
    peak = np.max(np.abs(out))
    if peak > 0:
        out /= peak
    return out


def _white_noise(duration_s: float, amplitude: float = 1.0) -> np.ndarray:
    return amplitude * np.random.default_rng(42).standard_normal(
        int(SAMPLE_RATE * duration_s)
    )


def _brown_noise(duration_s: float, amplitude: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(7)
    white = rng.standard_normal(int(SAMPLE_RATE * duration_s))
    brown = np.cumsum(white)
    brown /= np.max(np.abs(brown))
    return amplitude * brown


def _pink_noise(duration_s: float, amplitude: float = 1.0) -> np.ndarray:
    """Approximate pink noise by averaging offset white-noise copies."""
    n = int(SAMPLE_RATE * duration_s)
    rng = np.random.default_rng(13)
    pink = np.zeros(n)
    for k in (1, 2, 4, 8, 16):
        white = rng.standard_normal(n)
        # Simple low-pass by convolving with box kernel of length k*100
        kernel_len = k * 50
        pink += np.convolve(white, np.ones(kernel_len) / kernel_len, mode="same")
    peak = np.max(np.abs(pink))
    if peak > 0:
        pink /= peak
    return amplitude * pink


def _lowpass(signal: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """Very simple IIR single-pole low-pass filter."""
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    dt = 1.0 / SAMPLE_RATE
    alpha = dt / (rc + dt)
    out = np.zeros_like(signal)
    out[0] = signal[0]
    for i in range(1, len(signal)):
        out[i] = out[i - 1] + alpha * (signal[i] - out[i - 1])
    return out


def _to_audiosegment(samples: np.ndarray) -> AudioSegment:
    """Convert a float64 numpy array [-1,1] to a pydub AudioSegment (mono)."""
    samples = np.clip(samples, -1.0, 1.0)
    int16 = (samples * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16.tobytes())
    buf.seek(0)
    return AudioSegment.from_wav(buf)


def _export(samples: np.ndarray, filename: str, target_dbfs: float = -20.0) -> None:
    """Normalise and export samples to ambient/<filename>.mp3"""
    seg = _to_audiosegment(samples)
    # Convert to stereo for richer playback
    seg = seg.set_channels(2)
    # Normalise to target
    if seg.dBFS > float("-inf"):
        seg = seg.apply_gain(target_dbfs - seg.dBFS)
    out_path = AMBIENT_DIR / filename
    seg.export(str(out_path), format="mp3", bitrate="128k")
    print(f"  ✓ {out_path.name}  ({seg.duration_seconds:.1f}s, {seg.dBFS:.1f} dBFS)")


# ── per-theme generators ──────────────────────────────────────────────────────

def gen_arts() -> np.ndarray:
    """Arts, Culture & Digital Storytelling
    Warm piano-like chord: C major (C4, E4, G4) with bell harmonics.
    Slow attack, gentle sustain, natural release.
    """
    n = int(SAMPLE_RATE * DURATION_S)
    env = _adsr(n, attack_s=0.5, decay_s=0.4, sustain=0.75, release_s=1.5)

    # Piano-style additive synthesis — fundamental + harmonics decay faster
    chord = np.zeros(n)
    notes = [
        (261.63, 1.0),   # C4
        (329.63, 0.85),  # E4
        (392.00, 0.70),  # G4
        (523.25, 0.45),  # C5
        (659.25, 0.20),  # E5 (overtone)
    ]
    for freq, amp in notes:
        # Each harmonic has its own faster decay
        harm_env = _exp_decay(n, tau_s=2.0 - 0.3 * (freq / 261.63))
        tone = _sine(freq, DURATION_S, amp)
        chord += tone * harm_env

    # Very soft pink noise texture for room ambience
    noise = _pink_noise(DURATION_S, amplitude=0.04)

    combined = chord * env + noise
    return combined / np.max(np.abs(combined))


def gen_industry() -> np.ndarray:
    """Working Lands & Industry
    Low industrial drone with subtle harmonic shimmer and brown-noise texture.
    """
    n = int(SAMPLE_RATE * DURATION_S)

    # Low drone: 55 Hz (A1) fundamental + harmonics
    drone = _chord(
        [(55.0, 1.0), (110.0, 0.55), (165.0, 0.30), (220.0, 0.15)],
        DURATION_S,
    )

    # Slow tremolo on the drone (0.8 Hz amplitude modulation)
    t = np.linspace(0, DURATION_S, n)
    tremolo = 0.85 + 0.15 * np.sin(2.0 * np.pi * 0.8 * t)

    # Low-passed brown noise for machinery undertone
    noise = _lowpass(_brown_noise(DURATION_S, amplitude=0.35), cutoff_hz=200.0)

    # Wide ADSR — long fade in to avoid abruptness
    env = _adsr(n, attack_s=1.0, decay_s=0.5, sustain=0.8, release_s=1.5)

    combined = drone * tremolo * env + noise * env
    return combined / np.max(np.abs(combined))


def gen_civic() -> np.ndarray:
    """Community Tech & Governance
    Clean bell chime: professional and clear, like a meeting-room notification.
    """
    n = int(SAMPLE_RATE * DURATION_S)

    # Bell inharmonic partials (ratios relative to 440 Hz)
    # Classic tubular bell partial ratios: 1, 2.756, 5.404, 8.933
    base = 440.0  # A4
    bell_partials = [
        (base * 1.000, 1.00),
        (base * 2.756, 0.55),
        (base * 5.404, 0.28),
        (base * 8.933, 0.14),
    ]
    bell = _chord(bell_partials, DURATION_S)

    # Bell envelope: sharp attack, long exponential decay
    attack_env = np.ones(n)
    attack_n = int(SAMPLE_RATE * 0.01)  # 10 ms attack
    attack_env[:attack_n] = np.linspace(0.0, 1.0, attack_n)
    decay_env = _exp_decay(n, tau_s=1.8)
    env = attack_env * decay_env

    # Subtle second bell hit at 1.5s for civic double-chime feel
    bell2 = _chord([(base * 1.5, 0.6), (base * 1.5 * 2.756, 0.3)], DURATION_S)
    offset = int(SAMPLE_RATE * 1.5)
    shift_env = np.zeros(n)
    remaining = n - offset
    if remaining > 0:
        shift_env[offset:] = _exp_decay(remaining, tau_s=1.4)

    combined = bell * env + bell2 * shift_env * 0.5
    return combined / np.max(np.abs(combined))


def gen_indigenous() -> np.ndarray:
    """Indigenous Lands & Innovation
    Pentatonic wind tones with gentle water/breeze texture.
    Respectful, grounded, natural.
    """
    n = int(SAMPLE_RATE * DURATION_S)

    # G pentatonic scale: G3, A3, B3, D4, E4
    pentatonic = [
        (196.00, 0.70),  # G3
        (220.00, 0.55),  # A3
        (246.94, 0.65),  # B3
        (293.66, 0.50),  # D4
        (329.63, 0.40),  # E4
    ]

    # Arpeggiate rather than chord — enter one note at a time
    tones = np.zeros(n)
    entry_offsets = [0.0, 0.6, 1.1, 1.7, 2.4]
    for (freq, amp), offset_s in zip(pentatonic, entry_offsets):
        onset = int(SAMPLE_RATE * offset_s)
        tone_n = n - onset
        if tone_n <= 0:
            continue
        tone = _sine(freq, tone_n / SAMPLE_RATE, amp)
        env = _exp_decay(tone_n, tau_s=1.6)
        tones[onset:] += tone * env

    # Wind/water: low-passed pink noise, very gentle
    wind = _lowpass(_pink_noise(DURATION_S, amplitude=0.18), cutoff_hz=600.0)

    # Overall fade-in and fade-out
    overall_env = _adsr(n, attack_s=0.8, decay_s=0.2, sustain=0.9, release_s=1.5)

    combined = tones * overall_env + wind * overall_env
    peak = np.max(np.abs(combined))
    return combined / peak if peak > 0 else combined


def gen_wilderness() -> np.ndarray:
    """Wild Spaces & Outdoor Life
    Forest ambience: bird-like high chirps over a soft low drone.
    """
    n = int(SAMPLE_RATE * DURATION_S)
    rng = np.random.default_rng(99)

    # Low forest drone: wind through trees
    drone = _lowpass(_brown_noise(DURATION_S, amplitude=0.25), cutoff_hz=300.0)
    drone_env = _adsr(n, attack_s=0.6, decay_s=0.3, sustain=0.85, release_s=1.2)

    # Bird-like chirps: short sine bursts at bird frequencies
    birds = np.zeros(n)
    chirp_times_s = [0.3, 0.9, 1.5, 2.1, 2.8, 3.4, 4.0]
    chirp_freqs = [2800.0, 3200.0, 2600.0, 3500.0, 2900.0, 3100.0, 2700.0]
    for t_s, freq in zip(chirp_times_s, chirp_freqs):
        # Each chirp is a ~100 ms Gaussian-windowed sine
        onset = int(SAMPLE_RATE * t_s)
        chirp_len = int(SAMPLE_RATE * 0.12)
        if onset + chirp_len > n:
            continue
        t_chirp = np.linspace(0, 0.12, chirp_len)
        # Slight upward frequency sweep
        chirp = np.sin(2.0 * np.pi * (freq + 400.0 * t_chirp) * t_chirp)
        window = np.hanning(chirp_len)
        amplitude = 0.18 + 0.12 * rng.random()
        birds[onset : onset + chirp_len] += amplitude * chirp * window

    combined = drone * drone_env + birds
    return combined / np.max(np.abs(combined))


def gen_community() -> np.ndarray:
    """Cariboo Voices & Local News
    Warm morning small-town feel: welcoming F major chord with soft texture.
    """
    n = int(SAMPLE_RATE * DURATION_S)

    # F major: F3, A3, C4 — warm and approachable
    chord = _chord(
        [
            (174.61, 1.00),  # F3
            (220.00, 0.80),  # A3
            (261.63, 0.65),  # C4
            (349.23, 0.40),  # F4 octave
            (440.00, 0.20),  # A4 overtone
        ],
        DURATION_S,
    )

    # Warm envelope — medium attack, like a lazy morning
    env = _adsr(n, attack_s=0.7, decay_s=0.4, sustain=0.80, release_s=1.4)

    # Soft white noise for coffee-shop room texture (very quiet)
    noise = _lowpass(_white_noise(DURATION_S, amplitude=0.06), cutoff_hz=800.0)

    combined = chord * env + noise
    return combined / np.max(np.abs(combined))


def gen_futures() -> np.ndarray:
    """Resilient Rural Futures
    Electronic hum meets organic undertones: slightly detuned tones + nature texture.
    """
    n = int(SAMPLE_RATE * DURATION_S)
    t = np.linspace(0, DURATION_S, n)

    # Electronic base: slightly detuned pair of tones (chorus effect)
    freq_base = 220.0  # A3
    detune = 1.5  # Hz
    electronic = (
        0.6 * np.sin(2.0 * np.pi * freq_base * t)
        + 0.4 * np.sin(2.0 * np.pi * (freq_base + detune) * t)
        + 0.3 * np.sin(2.0 * np.pi * freq_base * 2 * t)
    )

    # Slow LFO wobble for electronic feel (0.4 Hz)
    lfo = 0.80 + 0.20 * np.sin(2.0 * np.pi * 0.4 * t)

    # Organic layer: low-passed pink noise for natural texture
    organic = _lowpass(_pink_noise(DURATION_S, amplitude=0.22), cutoff_hz=500.0)

    # Overall envelope
    env = _adsr(n, attack_s=0.6, decay_s=0.3, sustain=0.85, release_s=1.5)

    combined = (electronic * lfo * 0.7 + organic) * env
    return combined / np.max(np.abs(combined))


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
    print(f"Generating {len(THEMES)} themed ambient chimes → {AMBIENT_DIR}/\n")

    for filename, theme, generator in THEMES:
        print(f"[{theme}]")
        samples = generator()
        _export(samples, filename)

    print(f"\nDone. {len(THEMES)} files written to {AMBIENT_DIR}/")


if __name__ == "__main__":
    main()
