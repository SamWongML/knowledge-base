"""Tolerant parsing of small-model output: JSON replies, ``<think>`` blocks and ``[n]`` citations."""

from __future__ import annotations

import json
import re
from typing import Any

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_CITE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")

_OPEN, _CLOSE = "<think>", "</think>"


def strip_think(text: str) -> str:
    """Remove reasoning blocks; an unterminated ``<think>`` swallows the rest of the text."""
    text = _THINK_BLOCK.sub("", text)
    lower = text.lower()
    if _CLOSE in lower:  # opening tag was injected by the chat template
        text = text[lower.rindex(_CLOSE) + len(_CLOSE) :]
    elif _OPEN in lower:
        text = text[: lower.index(_OPEN)]
    return text.strip()


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of one JSON object. Returns None instead of raising."""
    text = strip_think(text)
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for cand in candidates:
        cand = cand.strip()
        start, end = cand.find("{"), cand.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            value = json.loads(cand[start : end + 1])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def cited_numbers(answer: str) -> list[int]:
    """``[n]`` / ``[n, m]`` markers in order of first appearance, without duplicates."""
    seen: dict[int, None] = {}
    for match in _CITE.finditer(answer):
        for part in match.group(1).split(","):
            seen.setdefault(int(part), None)
    return list(seen)


class ThinkFilter:
    """Incrementally drops ``<think>…</think>`` spans from a token stream.

    Tag fragments split across deltas are held back until they can be classified.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._thinking = False
        self._started = False

    def feed(self, delta: str) -> str:
        self._buf += delta
        out: list[str] = []
        while self._buf:
            lower = self._buf.lower()
            if self._thinking:
                i = lower.find(_CLOSE)
                if i < 0:
                    self._buf = self._buf[-(len(_CLOSE) - 1) :]
                    break
                self._buf = self._buf[i + len(_CLOSE) :]
                self._thinking = False
                continue
            i = lower.find(_OPEN)
            if i >= 0:
                out.append(self._buf[:i])
                self._buf = self._buf[i + len(_OPEN) :]
                self._thinking = True
                continue
            hold = _partial_suffix(lower, _OPEN)
            out.append(self._buf[: len(self._buf) - hold])
            self._buf = self._buf[len(self._buf) - hold :]
            break
        return self._emit("".join(out))

    def flush(self) -> str:
        rest = "" if self._thinking else self._buf
        self._buf = ""
        return self._emit(rest)

    def _emit(self, text: str) -> str:
        if not self._started:
            text = text.lstrip()
            self._started = bool(text)
        return text


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of ``text`` that is a proper prefix of ``tag``."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0
