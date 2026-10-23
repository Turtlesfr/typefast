"""Keyboard behaviour of the overlay, without any GTK, so it can be tested."""

from typefast import styles
from typefast.vocab import MAX_SELECTED, Vocabulary, find_match, fold

JUMP_RESET_S = 1.5


class OverlayModel:
    def __init__(self, langs: list[str], lang_index: int, vocab: Vocabulary, cols: int, lang_cols: int,
                 style: str = styles.DEFAULT_STYLE):
        self.langs = langs
        self.styles = list(styles.STYLES)
        self.style = self.styles.index(styles.normalize(style))
        self.lang = max(0, min(lang_index, len(langs) - 1))
        self.vocab = vocab
        self.cols = max(1, cols)
        self.lang_cols = max(1, lang_cols)
        self.zone = "lang"
        self.vi = 0
        self.jump = ""
        self.jump_at = 0.0
        self.miss = False
        self.message = ""
        self.changed = False          # the word list changed: the grid must be rebuilt
        self._undo: tuple[str, bool] | None = None

    @property
    def words(self) -> list[str]:
        return self.vocab.words

    @property
    def language(self) -> str:
        return self.langs[self.lang]

    @property
    def style_key(self) -> str:
        return self.styles[self.style]

    @property
    def typed_is_new(self) -> bool:
        """The typed letters are not a word yet, even if they start one ("Java" next to "JavaScript")."""
        text = fold(" ".join(self.jump.split()))
        return bool(text) and not any(fold(w) == text for w in self.words)

    def _clear_jump(self) -> None:
        self.jump, self.miss = "", False

    def _jump_to(self) -> None:
        i = find_match(self.words, self.jump)
        self.miss = i < 0
        if i >= 0:
            self.vi = i
            self.zone = "vocab"

    def _focus_word(self, word: str) -> None:
        for i, w in enumerate(self.words):
            if w == word:
                self.vi = i
                self.zone = "vocab"
                return

    # -- vocabulary edits -------------------------------------------------------

    def _add_typed(self) -> None:
        """Add the typed letters as a new word, tick it and focus it."""
        text = " ".join(self.jump.split())
        self._clear_jump()
        if not text:
            return
        existing = next((w for w in self.words if fold(w) == fold(text)), None)
        if existing is None:
            self.vocab.add(text)
            self.changed = True
            word = next((w for w in self.words if fold(w) == fold(text)), text)
            verb = "Added"
        else:
            word, verb = existing, "Already there:"
        if word not in self.vocab.selected and not self.vocab.toggle(word):
            self.message = f"{verb} “{word}”. It is not ticked: you can tick up to {MAX_SELECTED} words."
        else:
            self.message = f"{verb} “{word}”, ticked. Enter starts dictating."
        self.vocab.save()
        self._focus_word(word)

    def _delete_focused(self) -> None:
        if self.zone != "vocab" or not self.words:
            return
        word = self.words[self.vi]
        self._undo = (word, word in self.vocab.selected)
        self.vocab.remove([word])
        self.changed = True
        self.vi = min(self.vi, len(self.words) - 1) if self.words else 0
        if not self.words:
            self.zone = "lang"
        self.message = f"Deleted “{word}”. Ctrl+Z puts it back."

    def _undo_delete(self) -> None:
        if not self._undo:
            return
        word, was_selected = self._undo
        self._undo = None
        self.vocab.add(word)
        if was_selected and word not in self.vocab.selected:
            self.vocab.toggle(word)
        self.vocab.save()
        self.changed = True
        self._focus_word(word)
        self.message = f"Restored “{word}”."

    # -- keys ---------------------------------------------------------------------

    def key(self, name: str, char: str, now: float, ctrl: bool = False) -> str | None:
        """Apply one key press. Returns "start", "close" or None."""
        self.message = ""
        if name in ("Return", "KP_Enter"):
            # Letters that are not a word yet are a new word: Enter adds it instead of starting,
            # also when they are the start of a longer word. Ctrl+Enter ticks an exact match.
            if self.jump and (self.typed_is_new or ctrl):
                self._add_typed()
                return None
            return "start"
        if ctrl:
            if name in ("z", "Z"):
                self._undo_delete()
            return None
        if name == "Escape":
            if self.jump:
                self._clear_jump()
                return None
            return "close"
        if name.startswith(("Left", "Right", "Up", "Down")) or name in ("Tab", "ISO_Left_Tab"):
            self._clear_jump()
        if char and char.isprintable() and not char.isspace():
            # A word being typed that matches nothing is a new word: never cut it short.
            if now - self.jump_at > JUMP_RESET_S and not self.miss:
                self.jump = ""
            self.jump += char
            self.jump_at = now
            self._jump_to()
            return None
        if name == "space" and self.jump and self.miss:
            self.jump += " "  # a new word with several parts, like "Banco Estado"
            self.jump_at = now
            return None
        if name == "BackSpace":
            if self.jump:
                self.jump = self.jump[:-1]
                self.jump_at = now
                if self.jump.strip():
                    self._jump_to()
                else:
                    self._clear_jump()
            return None
        if name == "Delete":
            self._clear_jump()
            self._delete_focused()
            return None
        if self.zone == "style":
            self._style_key(name)
        elif self.zone == "lang":
            self._lang_key(name)
        else:
            self._vocab_key(name)
        return None

    def _style_key(self, name: str) -> None:
        n = len(self.styles)
        if name == "Left":
            self.style = (self.style - 1) % n
        elif name == "Right":
            self.style = (self.style + 1) % n
        elif name in ("Down", "Tab"):
            self.zone = "lang"

    def _lang_key(self, name: str) -> None:
        n = len(self.langs)
        if name == "Left":
            self.lang = (self.lang - 1) % n
        elif name == "Right":
            self.lang = (self.lang + 1) % n
        elif name == "Up":
            if self.lang - self.lang_cols >= 0:
                self.lang -= self.lang_cols
            else:
                self.zone = "style"  # the styles sit above the languages
        elif name in ("Down", "Tab"):
            if self.lang + self.lang_cols < n and name == "Down" and (self.lang // self.lang_cols) < (n - 1) // self.lang_cols:
                self.lang = min(n - 1, self.lang + self.lang_cols)
            elif self.words:
                self.zone = "vocab"

    def _vocab_key(self, name: str) -> None:
        n = len(self.words)
        if not n:
            self.zone = "lang"
            return
        if name == "Left":
            self.vi = max(0, self.vi - 1)
        elif name == "Right":
            self.vi = min(n - 1, self.vi + 1)
        elif name == "Down":
            if self.vi + self.cols < n:
                self.vi += self.cols
            elif self.vi // self.cols < (n - 1) // self.cols:
                self.vi = n - 1  # shorter last row
        elif name in ("Up", "ISO_Left_Tab"):
            if self.vi - self.cols >= 0 and name == "Up":
                self.vi -= self.cols
            else:
                self.zone = "lang"
        elif name == "space":
            word = self.words[self.vi]
            if not self.vocab.toggle(word):
                self.message = f"You can tick up to {MAX_SELECTED} words."
            self._clear_jump()
