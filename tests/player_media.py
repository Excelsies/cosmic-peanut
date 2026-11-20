"""Small audio files for the player tests, generated with GStreamer's own encoders.

numpy synthesises the PCM (so every click and every ramp value is known exactly); GStreamer
encodes, muxes and tags it. Only imported by tests that already have GStreamer.
"""

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

CLICK_RATE = 44100
CLICK_FIRST_S = 0.25
CLICK_PERIOD_S = 0.5
CLICK_SECONDS = 5.0
RAMP_SECONDS = 10.0
RAMP_SLOPE = 0.09  # sample value per second of the ramp file (0 → 0.9 over 10 s)
SHORT_SECONDS = 1.2

# A real 1x1 JPEG: a recognised media type that holds no audio.
_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////"
    "////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgB"
    "AQABPxA="
)


@dataclass(frozen=True, slots=True)
class Media:
    root: Path
    click: Path  # 120 BPM clicks, no tags
    ramp: Path  # value rises 0.09 per second: a block's mean tells where in the file it came from
    tone_a: Path  # FLAC, tags, 1.2 s, peak 0.5
    tone_b: Path  # MP3, tags, 1.2 s, peak 0.25
    tone_c: Path  # M4A/AAC, non-ASCII tags, 1.2 s
    tone_d: Path  # Ogg Vorbis, tags, 1.2 s
    mono: Path  # 22.05 kHz mono FLAC, no tags
    jpg: Path
    text: Path  # prose in a .mp3 file
    noise: Path  # random bytes in a .flac file
    truncated: Path  # the start of a FLAC, cut off
    missing: Path  # never created


def click_pcm() -> np.ndarray:
    n = int(CLICK_RATE * CLICK_SECONDS)
    x = np.zeros(n, dtype=np.float32)
    k = np.arange(441)
    burst = (0.9 * np.cos(2 * np.pi * 2000 * k / CLICK_RATE) * np.exp(-k / 66.0)).astype(np.float32)
    step = int(CLICK_PERIOD_S * CLICK_RATE)
    for start in range(int(CLICK_FIRST_S * CLICK_RATE), n - burst.size, step):
        x[start : start + burst.size] = burst
    return np.stack([x, x], axis=1)


def click_times() -> list[float]:
    """Seconds into the file at which each click starts."""
    n = int(CLICK_RATE * CLICK_SECONDS)
    step = int(CLICK_PERIOD_S * CLICK_RATE)
    return [s / CLICK_RATE for s in range(int(CLICK_FIRST_S * CLICK_RATE), n - 441, step)]


def ramp_pcm() -> np.ndarray:
    t = np.arange(int(CLICK_RATE * RAMP_SECONDS)) / CLICK_RATE
    x = (RAMP_SLOPE * t).astype(np.float32)
    return np.stack([x, x], axis=1)


def tone_pcm(freq: float, peak: float, rate: int = CLICK_RATE, channels: int = 2) -> np.ndarray:
    t = np.arange(int(rate * SHORT_SECONDS)) / rate
    x = (peak * np.sin(2 * np.pi * freq * t + 0.5)).astype(np.float32)
    return np.stack([x] * channels, axis=1)


def _tag_string(tags: dict[str, Any]) -> str:
    return ",".join(
        f"{k}=(uint){v}" if isinstance(v, int) else f'{k}="{v}"' for k, v in tags.items()
    )


def encode(
    gst: Any, out: Path, pcm: np.ndarray, rate: int, tail: str, tags: dict[str, Any] | None = None
) -> None:
    raw = out.with_suffix(".f32")
    pcm.astype("<f4").tofile(raw)
    desc = (
        f'filesrc location="{raw}" ! rawaudioparse format=pcm pcm-format=f32le '
        f"sample-rate={rate} num-channels={pcm.shape[1]} ! audioconvert dithering=none ! "
        f'taginject name=ti ! {tail} ! filesink location="{out}"'
    )
    pipe = gst.parse_launch(desc)
    if tags:
        pipe.get_by_name("ti").set_property("tags", _tag_string(tags))
    pipe.set_state(gst.State.PLAYING)
    msg = pipe.get_bus().timed_pop_filtered(
        30 * gst.SECOND, gst.MessageType.EOS | gst.MessageType.ERROR
    )
    pipe.set_state(gst.State.NULL)
    raw.unlink()
    if msg is None or msg.type == gst.MessageType.ERROR:
        raise RuntimeError(f"encoding {out.name} failed: {msg and msg.parse_error()}")


def build(gst: Any, root: Path) -> Media:
    root.mkdir(parents=True, exist_ok=True)
    m = Media(
        root=root,
        click=root / "click.flac",
        ramp=root / "ramp.flac",
        tone_a=root / "tone_a.flac",
        tone_b=root / "tone_b.mp3",
        tone_c=root / "tone_c.m4a",
        tone_d=root / "tone_d.ogg",
        mono=root / "mono.flac",
        jpg=root / "cover.jpg",
        text=root / "lyrics.mp3",
        noise=root / "noise.flac",
        truncated=root / "truncated.flac",
        missing=root / "missing.flac",
    )
    flac = "audio/x-raw,format=S16LE ! flacenc"
    encode(gst, m.click, click_pcm(), CLICK_RATE, flac)
    encode(gst, m.ramp, ramp_pcm(), CLICK_RATE, flac)
    encode(gst, m.mono, tone_pcm(440, 0.5, 22050, 1), 22050, flac)
    encode(
        gst, m.tone_a, tone_pcm(440, 0.5), CLICK_RATE, flac,
        {"artist": "Artist A", "album": "Album A", "title": "Track A", "track-number": 1},
    )  # fmt: skip
    encode(
        gst, m.tone_b, tone_pcm(660, 0.25), CLICK_RATE, "lamemp3enc target=quality quality=2 ! id3v2mux",
        {"artist": "  Artist B  ", "album": "Album B", "title": "Track B"},
    )  # fmt: skip
    encode(
        gst, m.tone_c, tone_pcm(880, 0.4), CLICK_RATE, "avenc_aac ! mp4mux",
        {"artist": "Sigur Rós", "album": "Ágætis byrjun", "title": "Svefn-g-englar ✓"},
    )  # fmt: skip
    encode(
        gst, m.tone_d, tone_pcm(330, 0.3), CLICK_RATE, "audioconvert ! vorbisenc quality=0.5 ! oggmux",
        {"artist": "Artist D", "title": "Track D"},
    )  # fmt: skip
    m.jpg.write_bytes(_JPEG)
    m.text.write_text("these are not audio samples\n" * 300)
    m.noise.write_bytes(np.random.default_rng(3).integers(0, 256, 60000, dtype=np.uint8).tobytes())
    data = m.tone_a.read_bytes()
    m.truncated.write_bytes(data[: len(data) // 3])
    return m
