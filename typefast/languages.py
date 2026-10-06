"""Languages TypeFast can dictate in.

The list is the intersection of what AssemblyAI's Universal-3.6 Pro streaming
model and its Dictation API both accept (32 languages as of October 2026).
Six of them also work with the faster word-by-word Universal-Streaming
Multilingual model.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str      # English name, used in settings
    native: str    # shown under the code in the overlay


LANGUAGES: tuple[Language, ...] = (
    Language("en", "English", "English"),
    Language("fr", "French", "Français"),
    Language("es", "Spanish", "Español"),
    Language("de", "German", "Deutsch"),
    Language("it", "Italian", "Italiano"),
    Language("pt", "Portuguese", "Português"),
    Language("nl", "Dutch", "Nederlands"),
    Language("ca", "Catalan", "Català"),
    Language("gl", "Galician", "Galego"),
    Language("ro", "Romanian", "Română"),
    Language("sv", "Swedish", "Svenska"),
    Language("no", "Norwegian", "Norsk"),
    Language("nn", "Norwegian Nynorsk", "Nynorsk"),
    Language("da", "Danish", "Dansk"),
    Language("fi", "Finnish", "Suomi"),
    Language("et", "Estonian", "Eesti"),
    Language("tr", "Turkish", "Türkçe"),
    Language("ru", "Russian", "Русский"),
    Language("ar", "Arabic", "العربية"),
    Language("he", "Hebrew", "עברית"),
    Language("fa", "Persian", "فارسی"),
    Language("ur", "Urdu", "اردو"),
    Language("hi", "Hindi", "हिन्दी"),
    Language("mr", "Marathi", "मराठी"),
    Language("vi", "Vietnamese", "Tiếng Việt"),
    Language("ja", "Japanese", "日本語"),
    Language("ko", "Korean", "한국어"),
    Language("zh", "Mandarin", "中文"),
    Language("yue", "Cantonese", "粵語"),
    Language("af", "Afrikaans", "Afrikaans"),
    Language("zu", "Zulu", "isiZulu"),
    Language("xh", "Xhosa", "isiXhosa"),
)

BY_CODE = {lang.code: lang for lang in LANGUAGES}

# Languages Universal-Streaming Multilingual handles (word-by-word partials).
WORD_BY_WORD = frozenset({"en", "es", "fr", "de", "it", "pt"})

MODEL_MULTILINGUAL = "universal-streaming-multilingual"
MODEL_U3_PRO = "universal-3-6-pro"


def get(code: str) -> Language:
    return BY_CODE.get(code) or Language(code, code, code)


def live_model_for(code: str, preference: str) -> str:
    """Pick the streaming model for a language.

    preference "auto" uses the word-by-word model when the language allows it,
    since its words appear one at a time and never get rewritten, which keeps
    live typing smooth. Anything else forces Universal-3.6 Pro.
    """
    if preference == "auto" and code in WORD_BY_WORD:
        return MODEL_MULTILINGUAL
    return MODEL_U3_PRO
