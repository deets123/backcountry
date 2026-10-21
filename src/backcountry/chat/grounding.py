"""The honesty check on chat replies, before anyone sees them.

A reply is withheld when it uses a number the facts don't have (at the reply's own precision), names a trail or
place the facts don't mention, or makes a claim this app can't support (safe, water there, open, legal, permitted,
conditions). Saying such things are unknown or not checked is fine: a sentence with that wording is exempt.
"""

import re

CANT_ANSWER = "I can't answer that from this trip's data."

_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.,])\d+(?:\.\d+)?")
_BANNED = [
    (r"\bsafe(ly)?\b", "says something is safe"),
    (r"\bwater (is|will be|should be) (available|flowing|drinkable|reliable|there|present)\b", "says water is there"),
    (r"\breliable water\b|\bwater (source|supply) is reliable\b", "says water is reliable"),
    (r"\b(is|are) (open|closed)\b", "says something is open or closed"),
    (r"\blegal(ly)?\b", "says something is legal"),
    (r"\bpermitted\b|\bno permits? (is |are )?(needed|required)\b|\bdon'?t need a permit\b", "makes a permit claim"),
    (r"\bconditions (are|will be)\b|\b(trail|route|path) is (clear|passable|in good (shape|condition))\b", "describes conditions"),
]
_UNKNOWN = re.compile(r"\bunknown\b|\bnot (known|checked)\b|\bcan'?t (tell|say|know|check)\b|\bcannot\b|"
                      r"\bdon'?t know\b|\bisn'?t known\b|\bhasn'?t checked\b|\bno (data|way to)\b|\baren'?t checked\b|"
                      r"\bnot confirmed\b|\bunconfirmed\b|\bwhether\b")
_NAME = re.compile(r"\b((?:[A-Z][\w'’-]*\s){1,4}(?:Trail|Trailhead|Run|Road|Creek|Ridge|Knob|Mountain|Lake|Pond|Spring|Grade|Rocks|Plains))\b")


def _value(token: str) -> tuple[float, float]:
    """The number and half a unit in its last digit (6.3 → 0.05, 784 → 0.5)."""
    decimals = len(token.split(".")[1]) if "." in token else 0
    return float(token.replace(",", "")), 0.5 * 10 ** -decimals + 1e-9


def _numbers(text: str) -> list[str]:
    return _NUMBER.findall(text)


def check_reply(reply: str, facts: str) -> tuple[bool, list[str]]:
    reasons = []
    known = [float(t.replace(",", "")) for t in _numbers(facts)]
    for token in _numbers(reply):
        v, tol = _value(token)
        if not any(abs(v - k) <= tol for k in known):
            reasons.append(f"unsupported number '{token}'")
    for sentence in re.split(r"(?<=[.!?;:])\s+", reply):
        if _UNKNOWN.search(sentence.lower()):
            continue
        for pattern, why in _BANNED:
            if re.search(pattern, sentence, re.IGNORECASE):
                reasons.append(why)
    facts_lower = facts.lower()
    for name in _NAME.findall(reply):
        words = name.split()
        while len(words) > 1 and " ".join(words).lower() not in facts_lower:
            words = words[1:]  # "The Red Creek Trail" → "Red Creek Trail"
        if len(words) < 2:  # only the bare "Trail"/"Run" was left, and that word alone proves nothing
            reasons.append(f"names '{name}', which isn't in this trip's data")
    return (not reasons, reasons)
