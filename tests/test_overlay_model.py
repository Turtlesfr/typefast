import pytest

from typefast.overlay_model import OverlayModel
from typefast.vocab import Vocabulary

WORDS = "Akeneo, AssemblyAI, Atlas Co, B2B, Beacon, BICE, Burgos, Cobalt, Las Condes, SAP, Santiago, Sundial"


@pytest.fixture
def model(tmp_path):
    v = Vocabulary(tmp_path / "v.json")
    v.add(WORDS)
    return OverlayModel(["fr", "es", "en"], 1, v, cols=5, lang_cols=3)


def press(m, *names, now=0.0):
    out = None
    for n in names:
        out = m.key(n, "", now)
    return out


def type_text(m, text, start=10.0):
    for i, ch in enumerate(text):
        m.key(ch, ch, start + i * 0.1)


def test_language_wraps_with_arrows(model):
    press(model, "Right")
    assert model.language == "en"
    press(model, "Right")
    assert model.language == "fr"
    press(model, "Left")
    assert model.language == "en"


def test_enter_starts_and_escape_closes(model):
    assert press(model, "Return") == "start"
    assert press(model, "Escape") == "close"


def test_down_enters_grid_and_up_leaves_it(model):
    press(model, "Down")
    assert model.zone == "vocab" and model.vi == 0
    press(model, "Down")
    assert model.vi == 5
    press(model, "Up", "Up")
    assert model.zone == "lang"


def test_down_on_short_last_row_goes_to_last_word(model):
    press(model, "Down")
    model.vi = 9  # row 1, column 4; row 2 has only 2 words (10, 11)
    press(model, "Down")
    assert model.vi == 11


def test_space_toggles_selection(model):
    press(model, "Down", "Right", "space")
    assert model.vocab.selected == {model.words[1]}
    press(model, "space")
    assert model.vocab.selected == set()


def test_type_ahead_jumps_and_space_clears_letters(model):
    type_text(model, "cond")
    assert model.zone == "vocab" and model.words[model.vi] == "Las Condes"
    press(model, "space")
    assert model.jump == "" and "Las Condes" in model.vocab.selected
    type_text(model, "sap", start=20.0)
    assert model.words[model.vi] == "SAP"


def test_type_ahead_resets_after_a_pause(model):
    model.key("s", "s", 0.0)
    model.key("y", "y", 5.0)  # long pause: a fresh search for "y"
    assert model.jump == "y"


def test_escape_clears_letters_before_closing(model):
    type_text(model, "zz")
    assert model.miss
    assert press(model, "Escape") is None and model.jump == ""
    assert press(model, "Escape") == "close"


def test_backspace_edits_search(model):
    type_text(model, "sab")
    assert model.miss
    press(model, "BackSpace", now=11.0)
    assert not model.miss and model.words[model.vi] in ("SAP", "Santiago")


def test_empty_vocabulary_stays_on_languages(tmp_path):
    m = OverlayModel(["en"], 0, Vocabulary(tmp_path / "v.json"), cols=5, lang_cols=1)
    press(m, "Down")
    assert m.zone == "lang"
    type_text(m, "abc")
    assert m.zone == "lang"


def test_many_languages_move_by_rows(tmp_path):
    v = Vocabulary(tmp_path / "v.json")
    v.add("one")
    m = OverlayModel(["en", "fr", "es", "de", "it", "pt", "nl", "ca"], 1, v, cols=5, lang_cols=6)
    press(m, "Down")
    assert m.language == "ca"  # second row
    press(m, "Down")
    assert m.zone == "vocab"


def test_enter_adds_unmatched_word_and_ticks_it(model):
    type_text(model, "Prisma")
    assert model.miss
    assert press(model, "Return") is None        # adds, does not start
    assert "Prisma" in model.words and "Prisma" in model.vocab.selected
    assert model.words[model.vi] == "Prisma" and model.changed
    assert press(model, "Return") == "start"     # second Enter starts dictating


def test_enter_with_matching_letters_starts(model):
    type_text(model, "sap")
    assert press(model, "Return") == "start"


def test_ctrl_enter_adds_even_when_letters_match(tmp_path):
    v = Vocabulary(tmp_path / "v.json")
    v.add("SAPIENS")
    m = OverlayModel(["en"], 0, v, cols=5, lang_cols=1)
    type_text(m, "SAP")
    assert not m.miss
    assert m.key("Return", "", 20.0, ctrl=True) is None
    assert "SAP" in m.words and "SAP" in m.vocab.selected


def test_space_continues_a_new_word(model):
    type_text(model, "Banco")
    press(model, "space", now=11.0)
    type_text(model, "Estado", start=11.1)
    press(model, "Return")
    assert "Banco Estado" in model.words


def test_new_word_is_not_cut_by_a_pause(model):
    model.key("Q", "Q", 0.0)
    model.key("x", "x", 5.0)   # "Q" matched nothing, so the pause keeps it
    assert model.jump == "Qx"


def test_adding_an_existing_word_just_ticks_it(model):
    model.vocab.selected.clear()
    model.key("Return", "", 0.0)  # nothing typed: starts
    type_text(model, "bice")      # matches BICE: Ctrl+Enter only ticks it
    model.key("Return", "", 20.0, ctrl=True)
    assert model.words.count("BICE") == 1 and "BICE" in model.vocab.selected


def test_delete_and_undo(model):
    press(model, "Down", "Right")
    word = model.words[1]
    model.vocab.toggle(word)
    press(model, "Delete")
    assert word not in model.words and model.changed
    assert model.message.startswith("Deleted")
    model.key("z", "", 1.0, ctrl=True)
    assert word in model.words and word in model.vocab.selected
    assert model.words[model.vi] == word


def test_delete_last_word_returns_to_languages(tmp_path):
    v = Vocabulary(tmp_path / "v.json")
    v.add("solo")
    m = OverlayModel(["en"], 0, v, cols=5, lang_cols=1)
    press(m, "Down", "Delete")
    assert m.words == [] and m.zone == "lang"


def test_first_word_can_be_added_from_an_empty_vocabulary(tmp_path):
    m = OverlayModel(["en"], 0, Vocabulary(tmp_path / "v.json"), cols=5, lang_cols=1)
    type_text(m, "Akeneo")
    assert m.key("Return", "", 30.0) is None
    assert m.words == ["Akeneo"] and m.zone == "vocab"


def test_deletion_is_saved(tmp_path):
    p = tmp_path / "v.json"
    v = Vocabulary(p)
    v.add("one, two")
    m = OverlayModel(["en"], 0, v, cols=5, lang_cols=1)
    press(m, "Down", "Delete")
    assert Vocabulary(p).words == ["two"]


def test_opens_on_language_with_clean_style(model):
    assert model.zone == "lang" and model.style_key == "clean"


def test_up_from_languages_reaches_styles(model):
    press(model, "Up")
    assert model.zone == "style"
    press(model, "Right")
    assert model.style_key == "formatted"
    press(model, "Right")
    assert model.style_key == "plain"        # wraps around
    press(model, "Down")
    assert model.zone == "lang" and model.style_key == "plain"
    assert press(model, "Return") == "start"


def test_enter_starts_from_the_style_row(model):
    press(model, "Up", "Left")
    assert model.style_key == "plain"
    assert press(model, "Return") == "start"


def test_typing_from_the_style_row_jumps_to_words(model):
    press(model, "Up")
    type_text(model, "sap")
    assert model.zone == "vocab" and model.words[model.vi] == "SAP"
