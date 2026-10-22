"""The honesty check on chat replies, before anyone sees them.

A reply is withheld when it uses a number the facts don't have (at the reply's own precision), names a trail or
place the facts don't mention, or makes a claim this app can't support (safe, water there, open, legal, permitted,
conditions), or converts to other units. Saying such things are unknown or not checked is fine: a clause with that
wording is exempt. A clause that calls day N by a difficulty level must use the level the facts give day N.
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
    (r"\bpermitted\b|(?<!\bif a )(?<!\bwhether a )(?<!\bif )(?<!\bwhether )\bpermits? (is |are )?(needed|required)\b|\b(do(n'?t)?|does(n'?t)?|will) (need|require) a permit\b|"
     r"\b(need|needs|require|requires) a permit\b", "makes a permit claim"),
    # "roads are allowed" is a setting, not a rule about the land, so roads are left out
    (r"\b(camping|camp|fires?|campfires?|dogs|pets|hunting|swimming|it|this|that|everything|anything|parking)"
     r" (is|are) (allowed|prohibited|banned)\b|\ballowed (to camp|anywhere)\b|\billegal\b", "says something is allowed or not"),
    (r"\bthere('s| is| are| will be) (plenty of |some |good )?water\b|\b(has|have) water\b|\bplenty of water\b|"
     r"\bfind water\b|\bwater (nearby|close by)\b", "says water is there"),
    (r"\bconditions (are|will be)\b|\b(trail|route|path) is (clear|passable|in good (shape|condition))\b", "describes conditions"),
]
_UNKNOWN = re.compile(r"\bunknown\b|\bnot (known|checked)\b|\bcan'?t (tell|say|know|check)\b|\bcannot\b|"
                      r"\bdon'?t know\b|\bisn'?t known\b|\bhasn'?t checked\b|\bno (data|way to)\b|\baren'?t checked\b|"
                      r"\bnot confirmed\b|\bunconfirmed\b|\bwhether\b")
_OTHER_UNITS = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s*(?:km|kilomet(?:er|re)s?|met(?:er|re)s?|m)\b", re.IGNORECASE)
_CLAUSE = re.compile(r"(?<=[.!?;:,])\s+|\s+(?:but|although|though)\s+", re.IGNORECASE)
_FACT_LEVEL = re.compile(r"^Day (\d+):.*, level (easy|moderate|hard|strenuous|unknown)\b", re.MULTILINE)
_DAY = re.compile(r"\bdays? (\d+(?:\s*(?:,|and|&|or)\s*\d+)*)\b", re.IGNORECASE)  # "day 2", "days 2 and 3"
_LEVEL_WORD = re.compile(r"\b(easy|moderate|hard|strenuous)\b(?!\s+to\b)", re.IGNORECASE)  # "hard to say" isn't a level
_NAME = re.compile(r"\b((?:[A-Z][\w'’-]*\s){1,4}(?:Trail|Trailhead|Run|Road|Creek|Ridge|Knob|Mountain|Lake|Pond|Spring|Grade|Rocks|Plains))\b")


def _value(token: str) -> tuple[float, float]:
    """The number and half a unit in its last digit (6.3 → 0.05, 784 → 0.5)."""
    decimals = len(token.split(".")[1]) if "." in token else 0
    return float(token.replace(",", "")), 0.5 * 10 ** -decimals + 1e-9


def _numbers(text: str) -> list[str]:
    return _NUMBER.findall(text)


def _level_claims(clause: str, levels: dict[int, str | None]) -> list[str]:
    """Each level word belongs to the closest day mention before it ("day 2", or every day of "days 2 and 3");
    "not hard" is no claim."""
    out = []
    mentions = [(m.start(), [int(x) for x in re.findall(r"\d+", m[1])]) for m in _DAY.finditer(clause)]
    for m in _LEVEL_WORD.finditer(clause):
        before = [days for at, days in mentions if at < m.start()]
        if not before or clause[:m.start()].lower().rstrip().endswith("not"):
            continue
        said = m[1].lower()
        for day in before[-1]:
            if day not in levels:
                continue
            actual = levels[day]
            why = (f"says day {day} is {said}, but its level is unknown" if actual is None else
                   f"says day {day} is {said}, but it is {actual}" if said != actual else None)
            if why and why not in out:
                out.append(why)
    return out


def check_reply(reply: str, facts: str, check_levels: bool = True) -> tuple[bool, list[str]]:
    """`check_levels=False` for a reply describing a proposed change: "I've set day 1 to easy" names a target,
    not the day's computed level."""
    reasons = []
    known = [float(t.replace(",", "")) for t in _numbers(facts)]
    for token in _numbers(reply):
        v, tol = _value(token)
        if not any(abs(v - k) <= tol for k in known):
            reasons.append(f"unsupported number '{token}'")
    if _OTHER_UNITS.search(reply):
        reasons.append("uses other units")  # the facts are in miles and feet; "19 km" is not "19 mi"
    for clause in _CLAUSE.split(reply):  # "unknown" wording excuses only its own clause
        if _UNKNOWN.search(clause.lower()):
            continue
        for pattern, why in _BANNED:
            if re.search(pattern, clause, re.IGNORECASE) and why not in reasons:
                reasons.append(why)
    levels = {int(d): (None if lvl == "unknown" else lvl) for d, lvl in _FACT_LEVEL.findall(facts)}
    if levels and check_levels:
        for clause in _CLAUSE.split(reply):
            reasons += [r for r in _level_claims(clause, levels) if r not in reasons]
    facts_lower = facts.lower()
    for name in _NAME.findall(reply):
        words = name.split()
        while len(words) > 1 and " ".join(words).lower() not in facts_lower:
            words = words[1:]  # "The Red Creek Trail" → "Red Creek Trail"
        if len(words) < 2:  # only the bare "Trail"/"Run" was left, and that word alone proves nothing
            reasons.append(f"names '{name}', which isn't in this trip's data")
    return (not reasons, reasons)
