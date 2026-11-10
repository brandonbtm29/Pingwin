"""Ratings by voice message: turn a Discord voice message into text on this PC, and find the score in it.

Speech to text runs locally with Whisper (the faster-whisper package), so recordings never leave the PC.
It's optional: without the package, Pingwin says it can't listen yet and people type their rating instead.
The model (about 0.5 GB) downloads into the project's models folder the first time someone uses it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import re
import time

from .config import PROJECT_ROOT

log = logging.getLogger("pingwin.voicerate")
MODEL = "small.en"
MODELS_DIR = PROJECT_ROOT / "models"
MAX_SECONDS = 180  # longer recordings aren't ratings
IDLE_UNLOAD = 20 * 60  # free the model's memory when nobody has used it for a while

WORDS = {
    "zero": 0,
    "nothing": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
NUMBER = r"\d+(?:\.\d+)?|" + "|".join(WORDS)
# "7", "7.5", "seven", "seven point five", "seven and a half", "seven and half"; not "one of", "two friends"
NOT_A_SCORE = r"(?!\s+(?:of|times?|hours?|minutes?|years?|people|friends|more|less|movies?|parts?|days?|weeks?)\b)"
SCORE = rf"(?P<n>{NUMBER})(?:\s+point\s+(?P<frac>{NUMBER}))?(?P<half>\s+and\s+(?:a\s+)?half)?"
OUT_OF = re.compile(rf"\b{SCORE}\s*(?:out\s+of|outta|/)\s*(?:10|ten)\b")
LEADS = (
    r"give\s+it|giving\s+it|gave\s+it|rate\s+it|rating\s+it|rated\s+it|rating\s+(?:is|of)|score\s+(?:is|of)|score|"
    r"it'?s\s+a|it\s+is\s+a|i'?d\s+say|call\s+it"
)
FILLER = r"(?:\s+(?:a|an|like|solid|strong|good|maybe|about|around|probably|just|easy|easily|high|low))*"
LEAD_IN = re.compile(rf"\b(?:{LEADS}){FILLER}\s+{SCORE}\b{NOT_A_SCORE}")
ANY = re.compile(rf"\b{SCORE}\b{NOT_A_SCORE}")


def _value(m: re.Match) -> float | None:
    def num(text: str) -> float:
        return float(WORDS[text]) if text in WORDS else float(text)

    try:
        v = num(m["n"])
        if m["frac"]:
            frac = m["frac"]
            v += num(frac) / (10 ** len(frac)) if frac not in WORDS else num(frac) / 10
        if m["half"]:
            v += 0.5
    except ValueError:
        return None
    return v if 0 <= v <= 10 else None


def spoken_score(text: str) -> float | None:
    """The score out of 10 someone said, like "I'd give it a seven and a half" or "8/10". None when there's
    no clear one: "out of ten" wins, then "give it a …", then a lone number from 0 to 10."""
    t = re.sub(r"[^\w./' ]+", " ", (text or "").lower().replace("-", " "))
    t = re.sub(r"(\d)\.(?!\d)", r"\1", t)  # "a 7." at the end of a sentence
    for pattern in (OUT_OF, LEAD_IN):
        for m in pattern.finditer(t):
            if (v := _value(m)) is not None:
                return round(v, 1)
    # A lone number, but not "one" on its own ("a fun one", "that one").
    found = {v for m in ANY.finditer(t) if m["n"] != "one" and (v := _value(m)) is not None}
    return round(found.pop(), 1) if len(found) == 1 else None


def available() -> bool:
    """Whether this PC can turn speech into text (the faster-whisper package is installed)."""
    return importlib.util.find_spec("faster_whisper") is not None


class Transcriber:
    """Whisper, loaded on first use and let go after a while without use. One recording at a time."""

    def __init__(self) -> None:
        self._model = None
        self._lock = asyncio.Lock()
        self.last_used = 0.0

    async def transcribe(self, audio: bytes) -> str:
        async with self._lock:
            self.last_used = time.time()
            try:
                return await asyncio.to_thread(self._run, audio)
            finally:
                self.last_used = time.time()

    def _run(self, audio: bytes) -> str:
        if self._model is None:
            from faster_whisper import WhisperModel  # optional: see available()

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            log.info("Loading the speech-to-text model (%s)", MODEL)
            self._model = WhisperModel(MODEL, device="cpu", compute_type="int8", download_root=str(MODELS_DIR))
        segments, _info = self._model.transcribe(io.BytesIO(audio), beam_size=5, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    def unload_if_idle(self, now: float | None = None) -> bool:
        if self._model is not None and not self._lock.locked() and (now or time.time()) - self.last_used > IDLE_UNLOAD:
            self._model = None
            log.info("Let go of the speech-to-text model (unused for a while)")
            return True
        return False
