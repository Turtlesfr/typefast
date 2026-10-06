"""Custom vocabulary: the words sent to AssemblyAI as keyterms."""

import re
import unicodedata

from typefast import config

MAX_SELECTED = 100      # AssemblyAI accepts at most 100 keyterms per request
MAX_TERM_LENGTH = 50    # longer streaming keyterms are ignored by the API


def fold(text: str) -> str:
    """Lowercase and strip accents, so "Évora" compares like "evora"."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def sort_key(word: str):
    return (fold(word), word)


def split_input(text: str) -> list[str]:
    """Split pasted text on commas, semicolons and new lines."""
    parts = re.split(r"[,;\n\r\t]+", text)
    return [" ".join(p.split()) for p in parts if p.strip()]


def find_match(words: list[str], query: str) -> int:
    """Type-ahead target for `query`, or -1.

    Tries, in order: the start of the whole term ("sa" finds "SAP"), the start
    of any word inside it ("cond" finds "Las Condes"), then anywhere.
    """
    q = fold(query)
    if not q:
        return -1
    folded = [fold(w) for w in words]
    for i, w in enumerate(folded):
        if w.startswith(q):
            return i
    for i, w in enumerate(folded):
        if any(part.startswith(q) for part in re.split(r"[\s.\-_/]+", w)):
            return i
    for i, w in enumerate(folded):
        if q in w:
            return i
    return -1


def match_span(word: str, query: str) -> tuple[int, int] | None:
    """Character range of `query` inside `word`, for highlighting."""
    q, w = fold(query), fold(word)
    if not q:
        return None
    # fold() can change length for some scripts; only highlight when it doesn't.
    if len(w) != len(word):
        return None
    if w.startswith(q):
        return (0, len(q))
    for m in re.finditer(r"[\s.\-_/]+", w):
        if w.startswith(q, m.end()):
            return (m.end(), m.end() + len(q))
    at = w.find(q)
    return (at, at + len(q)) if at >= 0 else None


class Vocabulary:
    """Word list plus the remembered selection, persisted to vocabulary.json."""

    def __init__(self, path=None):
        self.path = path or config.VOCABULARY_FILE
        data = config.read_json(self.path, {})
        self.words: list[str] = []
        self.selected: set[str] = set()
        for w in data.get("words", []):
            self._insert(w)
        self.selected = {w for w in data.get("selected", []) if w in self.words}

    def _insert(self, word: str) -> bool:
        word = " ".join(str(word).split())[:MAX_TERM_LENGTH]
        # "SAP" and "sap", or "Évora" and "Evora", count as the same word.
        if not word or any(fold(w) == fold(word) for w in self.words):
            return False
        self.words.append(word)
        self.words.sort(key=sort_key)
        return True

    def save(self) -> None:
        config.write_json(self.path, {
            "words": self.words,
            "selected": sorted(self.selected, key=sort_key),
        })

    def add(self, text: str) -> list[str]:
        added = [w for w in split_input(text) if self._insert(w)]
        if added:
            self.save()
        return added

    def remove(self, words) -> None:
        for w in words:
            if w in self.words:
                self.words.remove(w)
            self.selected.discard(w)
        self.save()

    def rename(self, old: str, new: str) -> bool:
        new = " ".join(new.split())[:MAX_TERM_LENGTH]
        if not new or old not in self.words:
            return False
        if fold(new) != fold(old) and any(fold(w) == fold(new) for w in self.words):
            return False
        was_selected = old in self.selected
        self.words.remove(old)
        self.selected.discard(old)
        self._insert(new)
        if was_selected:
            self.selected.add(new)
        self.save()
        return True

    def toggle(self, word: str) -> bool:
        """Flip selection. Returns False when the 100-term limit blocks it."""
        if word in self.selected:
            self.selected.discard(word)
            return True
        if len(self.selected) >= MAX_SELECTED:
            return False
        self.selected.add(word)
        return True

    def keyterms(self) -> list[str]:
        return sorted(self.selected, key=sort_key)[:MAX_SELECTED]
