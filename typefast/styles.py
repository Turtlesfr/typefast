"""Text styles: how much the final pass rewrites what was dictated."""

STYLES = ("plain", "clean", "formatted")
DEFAULT_STYLE = "clean"

LABELS = {"plain": "Plain", "clean": "Clean", "formatted": "Formatted"}
DESCRIPTIONS = {
    "plain": "Your words as transcribed, with punctuation and your vocabulary. No rewriting.",
    "clean": "Fillers and self-corrections removed, your wording kept.",
    "formatted": "Rewritten for email: smoother sentences, paragraphs, bullet points.",
}

# Used only by the backup rewrite: the Dictation API applies its own cleanup by default.
CLEAN_INSTRUCTION = (
    "Clean up this dictated text: remove filler words and hesitations, resolve self-corrections to what the "
    "speaker finally meant, and fix punctuation, capitalization and obvious mis-hearings. Keep the speaker's "
    "wording, tone and language. Do not add, summarize or translate anything."
)

DEFAULT_FORMATTED_INSTRUCTION = (
    "Rewrite this dictation as a polished written message, ready to send by email. Keep the same language, "
    "meaning, facts and tone, and keep every point the speaker made, including any greeting or closing they "
    "dictated. Do not add information, greetings or signatures that were not dictated. Improve "
    "sentence flow and word choice, remove fillers and resolve self-corrections. Use short paragraphs separated "
    "by a blank line. When the speaker lists several items or steps, put one per line, each starting with \"- \". "
    "Keep names and terms exactly as dictated. Output only the rewritten text."
)

MAX_INSTRUCTION = 2048  # Dictation API limit for llm_instruction

# Seconds after the user stops before the backup rewrite starts (None: no backup).
HEDGE_AFTER = {"plain": None, "clean": 2.5, "formatted": 8.0}
FORMATTED_MIN_DEADLINE_S = 25  # an email can wait a little longer than a chat message


def normalize(style: str | None) -> str:
    return style if style in STYLES else DEFAULT_STYLE


def formatted_instruction(cfg: dict) -> str:
    text = (cfg.get("formatted_instruction") or "").strip() or DEFAULT_FORMATTED_INSTRUCTION
    return text[:MAX_INSTRUCTION]


def dictation_instruction(style: str, cfg: dict) -> str | None:
    """llm_instruction for the Dictation API; None keeps its default cleanup."""
    return formatted_instruction(cfg) if style == "formatted" else None


def backup_instruction(style: str, cfg: dict) -> str | None:
    if style == "formatted":
        return formatted_instruction(cfg)
    if style == "clean":
        return CLEAN_INSTRUCTION
    return None


def deadline(style: str, cfg: dict) -> float:
    base = float(cfg.get("polish_timeout_s", 10))
    return max(base, FORMATTED_MIN_DEADLINE_S) if style == "formatted" else base


def hedge_after(style: str, cfg: dict) -> float | None:
    if not cfg.get("backup", True):
        return None
    return HEDGE_AFTER.get(style)


def pick_text(style: str, result) -> str:
    """Which version of a PolishResult goes into the field."""
    return result.verbatim if style == "plain" else result.clean
