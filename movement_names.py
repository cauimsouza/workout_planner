"""Standard form for movement names, e.g. "Pull-up" (not "Pull Up", "pull up" or "PullUp").

Rules:
1. Sentence case: only the first letter is capitalised ("Cable seated row"), except
   abbreviations typed in capitals (RDL, EZ) and proper names (Romanian, Zercher, ...).
2. "<word> up" movements are hyphenated: "Pull-up", "Chin-up", "Muscle-up", "Push-ups".
3. Single spaces, none just inside brackets: "Weighted muscle-up (swing back)".
4. Letters, digits, spaces and - ( ) ' / . , only; 2 to 60 characters; balanced brackets.
"""
import re

MIN_LENGTH = 2
MAX_LENGTH = 60

# Proper names that keep their capital letter. Matched case-insensitively.
PROPER_NAMES = {
    'arnold', 'bulgarian', 'copenhagen', 'cossack', 'hindu', 'jefferson', 'kroc', 'larsen',
    'meadows', 'nordic', 'pallof', 'pendlay', 'romanian', 'russian', 'scott', 'smith',
    'sorensen', 'spoto', 'turkish', 'yates', 'zercher',
}

ALLOWED = re.compile(r"[\w\s\-()'/.,]+")
CAMEL_CASE = re.compile(r'(?<=[a-z])(?=[A-Z])')
UP_WORD = re.compile(r"\b([^\W\d_]+)[\s-]+(ups?)\b", re.IGNORECASE)


class InvalidMovementName(ValueError):
    pass


def normalize_movement_name(raw: str) -> str:
    """Return the standard form of a movement name, or raise InvalidMovementName."""
    name = ' '.join(raw.split())
    if len(name) < MIN_LENGTH:
        raise InvalidMovementName('Movement names need at least 2 characters.')
    if len(name) > MAX_LENGTH:
        raise InvalidMovementName(f'Movement names can have at most {MAX_LENGTH} characters.')
    if not ALLOWED.fullmatch(name) or '_' in name:
        raise InvalidMovementName("Use only letters, digits, spaces and - ( ) ' / . ,")
    if not re.search(r'[^\W\d_]', name):
        raise InvalidMovementName('Movement names need at least one letter.')
    depth = 0
    for char in name:
        depth += {'(': 1, ')': -1}.get(char, 0)
        if depth < 0 or depth > 1:
            break
    if depth != 0:
        raise InvalidMovementName('Brackets must come in pairs, e.g. "Muscle-up (swing back)".')

    # Spacing: "Pull - up" -> "Pull-up", "( swing back )" -> "(swing back)", "row(cable)" -> "row (cable)"
    name = re.sub(r'\s*-\s*', '-', name)
    name = re.sub(r'\(\s*', '(', name)
    name = re.sub(r'\s*\)', ')', name)
    name = re.sub(r'(?<=[^\s(])\(', ' (', name)

    # A name typed all in capitals is shouting, not a list of abbreviations.
    shouting = name.upper() == name and len(re.findall(r'[^\W\d_]', name)) > 4

    words = []
    for word in re.split(r'(\s+|[-()/,.])', name):
        if not word or not re.search(r'[^\W\d_]', word):
            words.append(word)
            continue
        if not shouting and word.isupper() and 2 <= len(word) <= 4:
            words.append(word)  # Abbreviation: RDL, EZ, DB
            continue
        # "ChinUp" -> "chin up", so it gets hyphenated below
        words.append(CAMEL_CASE.sub(' ', word).lower())
    name = ''.join(words)

    name = UP_WORD.sub(lambda m: f'{m.group(1)}-{m.group(2).lower()}', name)
    name = re.sub(
        r"[^\W\d_]+",
        lambda m: m.group(0).capitalize() if m.group(0).lower() in PROPER_NAMES else m.group(0),
        name,
    )
    return name[0].upper() + name[1:]
