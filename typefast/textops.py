"""Text helpers: live transcript assembly, edit planning, final clean-up."""

import re
import unicodedata
from dataclasses import dataclass


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


# Spoken punctuation: what the speaker says -> what gets typed. Patterns are regexes
# over words; the transcriber may put its own punctuation between them ("point, à la ligne").
# A bare "point" / "period" / "punto" is left alone: it is an everyday word, and the
# transcriber already ends sentences with a period.
_SEP = r"[\s,.]+"
SPOKEN = {
    "fr": [
        (rf"point{_SEP}[àa]{_SEP}la{_SEP}ligne", ".\n"),
        (rf"point{_SEP}virgule", ";"),
        (rf"point{_SEP}d['’]\s*interrogation", "?"),
        (rf"point{_SEP}d['’]\s*exclamation", "!"),
        (rf"point{_SEP}final", "."),
        (rf"deux{_SEP}points", ":"),
        (r"virgule", ","),
        (rf"nouveau{_SEP}paragraphe", "\n\n"),
        (rf"(?:retour{_SEP})?[àa]{_SEP}la{_SEP}ligne|nouvelle{_SEP}ligne", "\n"),
    ],
    "en": [
        (rf"full{_SEP}stop", "."),
        (r"comma", ","),
        (r"semicolon|semi-colon", ";"),
        (r"colon", ":"),
        (rf"question{_SEP}mark", "?"),
        (rf"exclamation{_SEP}(?:mark|point)", "!"),
        (rf"new{_SEP}paragraph", "\n\n"),
        (rf"new{_SEP}line", "\n"),
    ],
    "es": [
        (rf"punto{_SEP}y{_SEP}aparte", ".\n"),
        (rf"punto{_SEP}y{_SEP}coma", ";"),
        (rf"punto{_SEP}final", "."),
        (rf"dos{_SEP}puntos", ":"),
        (r"coma", ","),
        (rf"signo{_SEP}de{_SEP}interrogaci[óo]n", "?"),
        (rf"signo{_SEP}de{_SEP}exclamaci[óo]n", "!"),
        (rf"nuevo{_SEP}p[áa]rrafo", "\n\n"),
        (rf"nueva{_SEP}l[íi]nea", "\n"),
    ],
}
_SPACE_BEFORE = {"fr": ";:?!"}  # French typography: "Pourquoi ?"
_PUNCT = r"[\s,.;:!?]*"


def _compile(language: str):
    rules = []
    for words, symbol in SPOKEN.get(language, ()):
        # Punctuation commands replace the transcriber's own punctuation on both sides;
        # line breaks keep the punctuation before them ("Merci. À la ligne." -> "Merci.\n").
        before = r"\s*" if symbol in ("\n", "\n\n") else _PUNCT
        rules.append((re.compile(rf"{before}\b(?:{words})\b{_PUNCT}(?P<next>\w?)", re.IGNORECASE), symbol))
    return rules


_RULES = {lang: _compile(lang) for lang in SPOKEN}


def spoken_punctuation(text: str, language: str) -> str:
    """Turn spoken punctuation ("virgule", "à la ligne", "new paragraph") into the real thing."""
    spaced = _SPACE_BEFORE.get(language, "")

    def replace(symbol: str):
        def sub(m: re.Match) -> str:
            nxt = m.group("next")
            breaks = symbol.endswith("\n")
            ends_sentence = breaks or symbol[-1] in ".?!"
            out = (" " if symbol in spaced else "") + symbol
            if not nxt:
                return out
            return out + ("" if breaks else " ") + (nxt.upper() if ends_sentence else nxt)
        return sub

    for pattern, symbol in _RULES.get(language, ()):
        text = pattern.sub(replace(symbol), text)
    return text.lstrip(" ")


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

    def __init__(self, language: str = ""):
        self.language = language  # for spoken punctuation
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
        return spoken_punctuation(nfc(" ".join(p for p in parts if p)), self.language)


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
