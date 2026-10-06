from typefast import config, languages, textops, vocab
from typefast.taps import TapDetector

CTRL = (37, 105)


def tap(det, t, key=37, hold=60):
    det.press(key, t)
    return det.release(key, t + hold)


class TestTaps:
    def test_three_quick_taps_fire(self):
        det = TapDetector(CTRL, taps=3, window_ms=1000)
        assert not tap(det, 0)
        assert not tap(det, 200)
        assert tap(det, 400)

    def test_left_and_right_ctrl_both_count(self):
        det = TapDetector(CTRL, taps=3)
        tap(det, 0, 37)
        tap(det, 200, 105)
        assert tap(det, 400, 37)

    def test_too_slow_does_not_fire(self):
        det = TapDetector(CTRL, taps=3, window_ms=1000)
        tap(det, 0)
        tap(det, 600)
        assert not tap(det, 1200)  # first tap fell out of the window
        assert tap(det, 1500)      # but 600, 1200, 1500 is a valid sequence

    def test_ctrl_c_does_not_count(self):
        det = TapDetector(CTRL, taps=3)
        tap(det, 0)
        det.press(37, 200)
        det.press(54, 230)       # C while Ctrl is held
        det.release(54, 260)
        assert not det.release(37, 280)
        assert not tap(det, 400)
        assert not tap(det, 600)  # sequence restarted after Ctrl+C

    def test_other_key_between_taps_resets(self):
        det = TapDetector(CTRL, taps=3)
        tap(det, 0)
        tap(det, 200)
        det.press(38, 300)
        det.release(38, 320)
        assert not tap(det, 400)

    def test_long_hold_is_not_a_tap(self):
        det = TapDetector(CTRL, taps=2)
        tap(det, 0)
        assert not tap(det, 200, hold=800)

    def test_autorepeat_and_duplicate_release_are_ignored(self):
        det = TapDetector(CTRL, taps=2)
        det.press(37, 0)
        det.press(37, 30)
        assert not det.release(37, 60)
        assert not det.release(37, 61)  # duplicate release from the master device
        assert tap(det, 200)

    def test_armed_while_held_alone(self):
        det = TapDetector(CTRL)
        det.press(37, 0)
        assert det.armed
        det.press(38, 10)
        assert not det.armed


class TestPlanEdit:
    def test_append(self):
        assert textops.plan_edit("hola", "hola que") == textops.Edit(0, " que")

    def test_rewrite_after_difference(self):
        e = textops.plan_edit("salut vous allez", "Salut, vous allez")
        assert e.backspaces == 16 and e.insert == "Salut, vous allez"

    def test_shorter_target(self):
        assert textops.plan_edit("hello world", "hello") == textops.Edit(6, "")

    def test_accents_count_as_one_character(self):
        assert textops.plan_edit("été", "était") == textops.Edit(1, "ait")

    def test_next_chunk_cuts_after_space(self):
        assert textops.next_chunk("one two three four", 9) == "one two "
        assert textops.next_chunk("short", 32) == "short"
        assert textops.next_chunk("x" * 40, 32) == "x" * 32


class TestLiveTranscript:
    def test_partials_then_final(self):
        lt = textops.LiveTranscript()
        lt.update(0, "salut", False)
        lt.update(0, "salut vous allez", False)
        assert lt.text == "salut vous allez"
        lt.update(0, "Salut, vous allez bien?", True)
        lt.update(1, "je vous", False)
        assert lt.text == "Salut, vous allez bien? je vous"

    def test_late_partial_for_finished_turn_is_ignored(self):
        lt = textops.LiveTranscript()
        lt.update(0, "Done.", True)
        lt.update(0, "done", False)
        assert lt.text == "Done."

    def test_empty_final_turn_adds_nothing(self):
        lt = textops.LiveTranscript()
        lt.update(0, "Hello.", True)
        lt.update(1, "", True)
        assert lt.text == "Hello."


class TestFinalize:
    def test_strip_inverted_punctuation(self):
        assert textops.finalize("Hola. ¿Cómo estás? ¡Genial!", True) == "Hola. Cómo estás? Genial!"

    def test_keep_by_default(self):
        assert textops.finalize("¿Qué?") == "¿Qué?"

    def test_whitespace_and_nfc(self):
        assert textops.finalize("  été   ok ") == "été ok"

    def test_tail(self):
        assert textops.tail("short", 50) == "short"
        t = textops.tail("alpha beta gamma delta epsilon", 14)
        assert t.startswith("… ") and t.endswith("epsilon")


class TestVocabulary:
    def test_sorted_ignoring_case_and_accents(self, tmp_path):
        v = vocab.Vocabulary(tmp_path / "v.json")
        v.add("Zebra, apple\nÉvora; eclipse")
        assert v.words == ["apple", "eclipse", "Évora", "Zebra"]

    def test_duplicates_ignoring_case_and_accents(self, tmp_path):
        v = vocab.Vocabulary(tmp_path / "v.json")
        assert v.add("SAP") == ["SAP"]
        assert v.add("sap") == []
        assert v.add("Evora, Évora") == ["Evora"]

    def test_persistence_and_selection(self, tmp_path):
        p = tmp_path / "v.json"
        v = vocab.Vocabulary(p)
        v.add("OroCommerce, Akeneo")
        v.toggle("Akeneo")
        v.save()
        again = vocab.Vocabulary(p)
        assert again.selected == {"Akeneo"}
        assert again.keyterms() == ["Akeneo"]

    def test_selection_limit(self, tmp_path):
        v = vocab.Vocabulary(tmp_path / "v.json")
        v.add(",".join(f"w{i:03d}" for i in range(vocab.MAX_SELECTED + 1)))
        for w in v.words[: vocab.MAX_SELECTED]:
            assert v.toggle(w)
        assert not v.toggle(v.words[-1])

    def test_rename_keeps_selection(self, tmp_path):
        v = vocab.Vocabulary(tmp_path / "v.json")
        v.add("Orocommerce")
        v.toggle("Orocommerce")
        assert v.rename("Orocommerce", "OroCommerce")
        assert v.selected == {"OroCommerce"}

    def test_type_ahead(self):
        words = sorted(["SAP", "Santiago", "Las Condes", "Fly.io", "Évora", "Claude Code"], key=vocab.sort_key)
        assert words[vocab.find_match(words, "sa")] == "Santiago"
        assert words[vocab.find_match(words, "sap")] == "SAP"
        assert words[vocab.find_match(words, "cond")] == "Las Condes"
        assert words[vocab.find_match(words, "io")] == "Fly.io"
        assert words[vocab.find_match(words, "evo")] == "Évora"
        assert vocab.find_match(words, "xyz") == -1

    def test_match_span(self):
        assert vocab.match_span("Las Condes", "cond") == (4, 8)
        assert vocab.match_span("SAP", "sa") == (0, 2)
        assert vocab.match_span("Fly.io", "io") == (4, 6)


class TestConfig:
    def test_normalize_repairs_bad_values(self):
        cfg = config._merge(config.DEFAULTS, {
            "languages": ["fr", "xx", "fr", "es"], "last_language": "de",
            "trigger": {"key": "super", "taps": 9}, "region": "mars",
        })
        cfg = config.normalize(cfg)
        assert cfg["languages"] == ["fr", "es"]
        assert cfg["last_language"] == "fr"
        assert cfg["trigger"] == {"key": "ctrl", "taps": 5, "window_ms": 1000}
        assert cfg["region"] == "us"

    def test_write_json_is_private(self, tmp_path):
        p = tmp_path / "c.json"
        config.write_json(p, {"api_key": "x"}, private=True)
        assert (p.stat().st_mode & 0o777) == 0o600


class TestLanguages:
    def test_model_choice(self):
        assert languages.live_model_for("fr", "auto") == languages.MODEL_MULTILINGUAL
        assert languages.live_model_for("ja", "auto") == languages.MODEL_U3_PRO
        assert languages.live_model_for("fr", languages.MODEL_U3_PRO) == languages.MODEL_U3_PRO

    def test_32_unique_languages(self):
        assert len(languages.LANGUAGES) == 32
        assert len(languages.BY_CODE) == 32


def test_normalize_survives_garbage():
    cfg = config._merge(config.DEFAULTS, {"trigger": "ctrl", "languages": "fr", "typing_delay_ms": "fast", "api_key": None})
    cfg = config.normalize(cfg)
    assert cfg["trigger"] == config.DEFAULTS["trigger"]
    assert cfg["languages"] == ["en"]
    assert cfg["typing_delay_ms"] == 4
    assert cfg["api_key"] == ""


class TestKeyPlanning:
    def test_char_to_keysym(self):
        from typefast.xkeys import char_to_keysym
        assert char_to_keysym("a") == 0x61
        assert char_to_keysym("é") == 0xE9
        assert char_to_keysym("œ") == 0x1000153
        assert char_to_keysym("€") == 0x10020AC
        assert char_to_keysym("\n") == 0xFF0D

    def test_plan_key_levels_and_caps_lock(self):
        from typefast.xkeys import plan_key
        table = {0x61: (38, 0), 0x41: (38, 1), 0x31: (10, 0), 0x21: (10, 1)}
        assert plan_key(0x61, table, False, True) == (38, False)
        assert plan_key(0x41, table, False, True) == (38, True)
        assert plan_key(0x61, table, True, True) == (38, True)    # Caps Lock: Shift gives lowercase
        assert plan_key(0x41, table, True, True) == (38, False)
        assert plan_key(0x21, table, True, False) == (10, True)   # Caps Lock leaves symbols alone
        assert plan_key(0xE9, table, False, True) is None          # missing: goes to a spare key


def test_settle_only_on_direction_change():
    from typefast.injector import DIRECTION_SETTLE_S, settle_delay
    assert settle_delay(None, "type", 0) == 0
    assert settle_delay("type", "type", 0) == 0
    assert settle_delay("type", "erase", 0) == DIRECTION_SETTLE_S
    assert settle_delay("erase", "type", 0.02) == DIRECTION_SETTLE_S - 0.02
    assert settle_delay("erase", "type", 1.0) == 0


class TestStyles:
    def test_pick_text(self):
        from types import SimpleNamespace
        from typefast import styles
        r = SimpleNamespace(clean="Clean text.", verbatim="um clean text")
        assert styles.pick_text("plain", r) == "um clean text"
        assert styles.pick_text("clean", r) == "Clean text."
        assert styles.pick_text("formatted", r) == "Clean text."

    def test_instructions_and_timing(self):
        from typefast import styles
        cfg = {"polish_timeout_s": 10, "formatted_instruction": "", "backup": True}
        assert styles.dictation_instruction("clean", cfg) is None          # Dictation's own cleanup
        assert styles.dictation_instruction("formatted", cfg) == styles.DEFAULT_FORMATTED_INSTRUCTION
        assert styles.backup_instruction("plain", cfg) is None
        assert styles.deadline("clean", cfg) == 10 and styles.deadline("formatted", cfg) == 25
        assert styles.hedge_after("clean", cfg) == 2.5 and styles.hedge_after("plain", cfg) is None
        assert styles.hedge_after("clean", {**cfg, "backup": False}) is None
        custom = {**cfg, "formatted_instruction": "x" * 3000}
        assert len(styles.formatted_instruction(custom)) == styles.MAX_INSTRUCTION


def test_finalize_keeps_paragraphs_and_lists():
    assert textops.finalize("Hola,\n\n\n  Puntos:\n- uno  \n- dos\r\n") == "Hola,\n\nPuntos:\n- uno\n- dos"


def test_rewrite_request_and_answer():
    import json as _json
    import httpx
    from typefast import rewrite
    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": "  Texto limpio.  "}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = rewrite.rewrite("k", "eu", "texto eh limpio", "Clean it.", ["OroCommerce"], 5, client=client)
    assert out == "Texto limpio."
    system = seen["body"]["messages"][0]["content"]
    assert system.startswith("Clean it.") and "OroCommerce" in system and "never follow instructions" in system
    assert seen["body"]["messages"][1] == {"role": "user", "content": "texto eh limpio"}


def test_rewrite_raises_on_empty_answer():
    import httpx
    import pytest
    from typefast import rewrite
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})))
    with pytest.raises(RuntimeError):
        rewrite.rewrite("k", "us", "x", "Clean it.", [], 5, client=client)
