import re


def normalize_wake_phrase(raw: str) -> str | None:
    """Same syntax as androsaur-audio WakePhrase; the audio module also validates on apply."""
    text = " ".join(re.sub(r"[,.!?]", " ", raw.lower()).split())
    words = text.split()
    if not text or len(text) > 80 or len(words) > 4:
        return None
    if not all(
        re.fullmatch(r"[а-яё]{2,20}(~[0-3])?", variant)
        for word in words
        for variant in word.split("|")
    ):
        return None
    return text
