"""Text helpers: live transcript assembly, edit planning, final clean-up."""

import unicodedata
from dataclasses import dataclass


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@dataclass
class Edit:
    """Turn `typed` into the target: press Backspace `backspaces` times, then type `insert`."""
    backspaces: int
    insert: str


def plan_edit(typed: str, target: str) -> Edit:
    """Smallest edit reachable with the caret at the end of the typed text.

    Only Backspace and typing are available, so everything after the first
    difference is deleted and retyped.
    """
    n = 0
    limit = min(len(typed), len(target))
    while n < limit and typed[n] == target[n]:
        n += 1
    return Edit(len(typed) - n, target[n:])


def next_chunk(text: str, max_len: int = 32) -> str:
    """Leading piece of `text` to type in one go, cut after a space when possible."""
    if len(text) <= max_len:
        return text
    cut = text.rfind(" ", 0, max_len)
    return text[: cut + 1] if cut > 0 else text[:max_len]


class LiveTranscript:
    """Builds the session text from streaming turns.

    Each turn grows through partial updates and ends with a final, formatted
    version. Finals stay; the current partial is replaced on every update.
    """

    def __init__(self):
        self.finals: dict[int, str] = {}
        self.partial_order: int | None = None
        self.partial = ""

    def update(self, turn_order: int, transcript: str, final: bool) -> None:
        text = " ".join(transcript.split())
        if final:
            self.finals[turn_order] = text
            if self.partial_order == turn_order:
                self.partial_order, self.partial = None, ""
        elif turn_order not in self.finals:
            self.partial_order, self.partial = turn_order, text

    @property
    def committed(self) -> str:
        return " ".join(t for _, t in sorted(self.finals.items()) if t)

    @property
    def text(self) -> str:
        parts = [self.committed, self.partial]
        return nfc(" ".join(p for p in parts if p))


def finalize(text: str, strip_inverted: bool = False) -> str:
    """Clean polished text before it is typed. Keeps paragraphs and list lines."""
    text = nfc(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if strip_inverted:
        text = text.replace("¿", "").replace("¡", "")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out = "\n".join(lines).strip("\n")
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip()


def join_segments(parts: list[str]) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


def tail(text: str, max_chars: int) -> str:
    """Last `max_chars` of text for the HUD, cut at a word boundary."""
    if len(text) <= max_chars:
        return text
    cut = text[-max_chars:]
    space = cut.find(" ")
    if 0 <= space < 20:
        cut = cut[space + 1:]
    return "… " + cut
