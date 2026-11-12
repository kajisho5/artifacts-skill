"""Generated text checked against its source (ecosystem roadmap step 7).

A summary, narration script or description written from a transcript or a
slide deck must not state anything the source does not. This module pulls
the claims that matter most out of the generated text and looks for each
one in the source files the caller names:

  - every number with a unit ("40 mg", "2.5 mL/kg", "12 %", "3 days",
    "120 patients", "5mg", "3日間"), ranges included ("5-10 mg" gives
    "5 mg" and "10 mg");
  - every date ("2026-03-12", "12 March 2026", "March 2026", "2026年3月12日");
  - every drug name it can recognise: a word ending in an international
    nonproprietary name stem (-mab, -nib, -pril, -sartan, -olol, -statin,
    -cillin, -mycin, -azole, -vir, ...) or one of a short list of common
    drugs without such a stem (aspirin, warfarin, insulin, morphine, ...);
  - every person's name after a title or honorific ("Dr. Jane Smith",
    "Prof. Tanaka", "山田教授");
  - every term in the policy's `grounding_terms` list (product names, drug
    names in any script, people named without a title, ...).

A claim is supported when the same claim appears in a source: numbers are
compared by value and unit after normalisation (NFKC width folding, "1,000"
== "1000", "5.0" == "5", "mcg" == "µg", "hours" == "h"), dates by their
year, month and day (a less specific date, such as "March 2026", is
supported by a more specific one in the source), and names and terms as
case-insensitive text. Every claim that is not supported is listed in the
check's evidence with the line it is on. Nothing is ever changed: this
check reports, the caller decides what to fix.

Deliberately a small set of literal patterns rather than an NLP model, for
the same reason as `leftover_text.py`: every finding has to be explainable
by a rule a reviewer can read. The cost is coverage. A number with no unit
("in 3 of the groups"), a name with no title and not listed in
`grounding_terms`, and a drug name with no known stem are not checked;
docs/verification.md states this next to the check.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

from artifact_skill.core.errors import ArtifactError, ArtifactInputError
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult

if TYPE_CHECKING:
    from artifact_skill.adapters.base import ArtifactAdapter
    from artifact_skill.core.artifact import ArtifactRef

GROUNDING_POLICY_KEYS = frozenset({"grounding_sources", "grounding_terms"})
CHECK_ID = "source_grounding"
CHECK_NAME = "Numbers, dates, names and terms appear in the source"

# ---- units -----------------------------------------------------------------

# written form (lower case, after NFKC) -> canonical form used for comparison
_ASCII_UNITS: dict[str, str] = {
    # compound dosing and lab units
    "mg/kg/day": "mg/kg/day", "mg/kg/d": "mg/kg/day", "mg/kg": "mg/kg", "μg/kg": "μg/kg", "mcg/kg": "μg/kg",
    "ug/kg": "μg/kg", "mg/m2": "mg/m2", "mg/dl": "mg/dl", "mg/ml": "mg/ml", "mg/l": "mg/l", "g/dl": "g/dl",
    "g/l": "g/l", "mmol/l": "mmol/l", "μmol/l": "μmol/l", "nmol/l": "nmol/l", "meq/l": "meq/l", "iu/ml": "iu/ml",
    "ml/min": "ml/min", "ml/kg": "ml/kg", "ml/h": "ml/h", "l/min": "l/min", "mg/day": "mg/day", "mg/d": "mg/day",
    "mg/h": "mg/h", "μg/ml": "μg/ml", "mcg/ml": "μg/ml",
    # mass and volume
    "mcg": "μg", "μg": "μg", "ug": "μg", "micrograms": "μg", "microgram": "μg",
    "mg": "mg", "milligrams": "mg", "milligram": "mg", "ng": "ng", "kg": "kg", "kilograms": "kg", "kilogram": "kg",
    "g": "g", "grams": "g", "gram": "g", "ml": "ml", "milliliters": "ml", "millilitres": "ml", "milliliter": "ml",
    "millilitre": "ml", "μl": "μl", "dl": "dl", "l": "l", "liters": "l", "litres": "l", "liter": "l", "litre": "l",
    "mmol": "mmol", "μmol": "μmol", "nmol": "nmol", "mol": "mol", "meq": "meq", "iu": "iu", "units": "unit",
    "unit": "unit",
    # vital signs, energy, length, temperature, share
    "mmhg": "mmhg", "bpm": "bpm", "kcal": "kcal", "cm": "cm", "mm": "mm", "km": "km", "°c": "°c", "°f": "°f",
    "%": "%", "percent": "%", "per cent": "%",
    # time
    "hours": "h", "hour": "h", "hrs": "h", "hr": "h", "h": "h", "minutes": "min", "minute": "min", "mins": "min",
    "min": "min", "seconds": "s", "second": "s", "sec": "s", "days": "day", "day": "day", "weeks": "week",
    "week": "week", "wk": "week", "months": "month", "month": "month", "years": "year", "year": "year", "yr": "year",
    # counts
    "patients": "patient", "patient": "patient", "participants": "participant", "participant": "participant",
    "subjects": "subject", "cases": "case", "times": "time", "tablets": "tablet", "tablet": "tablet",
    "doses": "dose", "dose": "dose", "drops": "drop", "puffs": "puff",
}
_CJK_UNITS: dict[str, str] = {
    "パーセント": "%", "日間": "day", "日": "day", "週間": "week", "週": "week", "か月": "month", "ヶ月": "month",
    "カ月": "month", "ヵ月": "month", "年間": "year", "年": "year", "時間": "h", "分": "min", "秒": "s",
    "人": "person", "名": "person", "例": "case", "歳": "age", "才": "age", "回": "time", "錠": "tablet",
    "倍": "times-fold", "円": "yen", "件": "item",
}


def _alternation(words: dict[str, str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_UNIT = rf"(?:{_alternation(_ASCII_UNITS)})(?![a-z])|(?:{_alternation(_CJK_UNITS)})"
# A number not glued to a preceding Latin letter, digit or decimal point.
# Deliberately ASCII-only: Japanese text puts a number straight after a
# kanji ("1日5mg"), which must still count.
_LEAD = r"(?<![0-9A-Za-z.,])"
_RANGE_RE = re.compile(
    rf"{_LEAD}(?P<a>{_NUM})\s?(?:-|\u2013|\u2014|~|\u301c|to|から)\s?(?P<b>{_NUM})\s?(?P<unit>{_UNIT})", re.IGNORECASE
)
_QUANTITY_RE = re.compile(rf"{_LEAD}(?P<num>{_NUM})\s?(?P<unit>{_UNIT})", re.IGNORECASE)

# ---- dates -----------------------------------------------------------------

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
    "nov": 11, "dec": 12,
}
_MONTH = rf"(?P<month>{'|'.join(sorted(_MONTHS, key=len, reverse=True))})\.?"
_ORD = r"(?:st|nd|rd|th)?"
_DATE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!\d)(?P<year>\d{4})[-/.](?P<mnum>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<year>\d{4})\s?年\s?(?P<mnum>\d{1,2})\s?月(?:\s?(?P<day>\d{1,2})\s?日)?"),
    re.compile(rf"(?<!\d)(?P<day>\d{{1,2}}){_ORD}\s+(?:of\s+)?{_MONTH},?\s+(?P<year>\d{{4}})(?!\d)", re.IGNORECASE),
    re.compile(rf"\b{_MONTH}\s+(?P<day>\d{{1,2}}){_ORD},?\s+(?P<year>\d{{4}})(?!\d)", re.IGNORECASE),
    re.compile(rf"\b{_MONTH},?\s+(?P<year>\d{{4}})(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)(?P<mnum>\d{1,2})\s?月\s?(?P<day>\d{1,2})\s?日"),
)

# ---- drugs -----------------------------------------------------------------

_DRUG_STEMS = (
    "mab", "nib", "parib", "pril", "sartan", "olol", "statin", "cillin", "mycin", "micin", "cycline", "floxacin",
    "azole", "tidine", "dipine", "oxetine", "triptan", "parin", "vir", "gliptin", "gliflozin", "glutide",
    "lukast", "semide", "thiazide", "platin", "taxel", "rubicin", "profen", "coxib", "olone", "sone", "zepam",
    "zolam", "barbital", "caine", "xaban", "gatran", "formin", "grel", "dronate", "terol", "setron", "afil",
)
_DRUG_NAMES = frozenset({
    "aspirin", "insulin", "warfarin", "morphine", "paracetamol", "acetaminophen", "digoxin", "levothyroxine",
    "methotrexate", "cyclosporine", "ciclosporin", "tacrolimus", "adrenaline", "epinephrine", "noradrenaline",
    "norepinephrine", "nitroglycerin", "codeine", "fentanyl", "oxycodone", "tramadol", "ketamine", "propofol",
    "naloxone", "amiodarone", "allopurinol", "colchicine", "heparin", "penicillin", "lithium carbonate",
})
# Ordinary words that happen to end in a drug stem.
_NOT_DRUGS = frozenset({"cholesterol"})
_WORD_RE = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z-]{4,}(?![A-Za-z])")

# ---- people ----------------------------------------------------------------

_PERSON_EN_RE = re.compile(
    r"\b(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx|Sir|Dame)\.?\s+(?P<name>(?:[A-Z]\.\s*|[A-Z][a-z]+\s+){0,2}[A-Z][A-Za-z'-]+)"
)
# The kanji run before the honorific can carry more than the name
# ("大学山田教授"); the source supports it when any ending of the run, two
# characters or longer, appears there (see _SourceIndex.supports).
_PERSON_JA_RE = re.compile(r"(?P<name>[一-鿿々]{2,8})\s?(?:准教授|教授|先生|医師|博士|氏)")


@dataclass
class Claim:
    kind: str  # "number" | "date" | "drug" | "person" | "term"
    text: str  # as written in the generated document (after width folding)
    key: Any  # what is compared with the source
    lines: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "lines": self.lines}


def _fold(text: str) -> str:
    # NFKC folds full-width digits, letters and the full-width percent sign
    # to ASCII and the micro sign to Greek mu, so a full-width "5 mg" and
    # "5 mg" compare equal.
    return unicodedata.normalize("NFKC", text)


def _number_key(num: str) -> str | None:
    try:
        value = Decimal(num.replace(",", "")).normalize()
    except InvalidOperation:
        return None
    return format(value, "f")


def _unit_key(unit: str) -> str:
    u = unit.lower()
    return _ASCII_UNITS.get(u) or _CJK_UNITS.get(unit) or u


def _mask(line: str, start: int, end: int) -> str:
    return line[:start] + " " * (end - start) + line[end:]


def _date_key(m: re.Match[str]) -> tuple[int | None, int | None, int | None] | None:
    groups = m.groupdict()
    year = int(groups["year"]) if groups.get("year") else None
    if groups.get("month"):
        month: int | None = _MONTHS[groups["month"].lower()]
    elif groups.get("mnum"):
        month = int(groups["mnum"])
    else:
        month = None
    day = int(groups["day"]) if groups.get("day") else None
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and not 1 <= day <= 31:
        return None
    return (year, month, day)


def _is_drug(word: str) -> bool:
    w = word.lower()
    if w in _NOT_DRUGS:
        return False
    return w in _DRUG_NAMES or (len(w) >= 6 and w.endswith(_DRUG_STEMS))


def _text_pattern(term: str) -> re.Pattern[str]:
    body = re.escape(term)
    if term.isascii():
        return re.compile(rf"(?<![0-9A-Za-z]){body}(?![0-9A-Za-z])", re.IGNORECASE)
    return re.compile(body, re.IGNORECASE)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def extract_claims(text: str, terms: list[str] | tuple[str, ...] = ()) -> list[Claim]:
    """Every checkable claim in `text`, one entry per distinct claim, in
    order of first appearance, each with the lines it appears on."""
    found: dict[tuple[str, Any], Claim] = {}

    def add(kind: str, written: str, key: Any, line_no: int) -> None:
        claim = found.setdefault((kind, key), Claim(kind=kind, text=written.strip(), key=key))
        if line_no not in claim.lines:
            claim.lines.append(line_no)

    folded_terms = [_fold(t).strip() for t in terms if _fold(t).strip()]
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = _fold(raw)
        for term in folded_terms:
            if _text_pattern(term).search(line):
                add("term", term, term.lower(), line_no)
        for m in _PERSON_EN_RE.finditer(line):
            add("person", m.group(0), _collapse(m.group("name")).lower(), line_no)
        for m in _PERSON_JA_RE.finditer(line):
            add("person", m.group(0), m.group("name"), line_no)
        for date_re in _DATE_RES:
            for m in date_re.finditer(line):
                key = _date_key(m)
                if key is not None:
                    add("date", m.group(0), key, line_no)
                    line = _mask(line, m.start(), m.end())
        for m in _RANGE_RE.finditer(line):
            unit = _unit_key(m.group("unit"))
            for part in ("a", "b"):
                value = _number_key(m.group(part))
                if value is not None:
                    add("number", f"{m.group(part)} {m.group('unit')}", (value, unit), line_no)
            line = _mask(line, m.start(), m.end())
        for m in _QUANTITY_RE.finditer(line):
            value = _number_key(m.group("num"))
            if value is not None:
                add("number", m.group(0), (value, _unit_key(m.group("unit"))), line_no)
        for m in _WORD_RE.finditer(line):
            if _is_drug(m.group(0)):
                add("drug", m.group(0), m.group(0).lower(), line_no)
    return list(found.values())


@dataclass
class _SourceIndex:
    numbers: set[tuple[str, str]]
    dates: list[tuple[int | None, int | None, int | None]]
    text: str  # folded, whitespace-collapsed, for name/term/drug lookups

    @classmethod
    def build(cls, texts: list[str], terms: list[str]) -> _SourceIndex:
        joined = "\n".join(texts)
        claims = extract_claims(joined, terms)
        return cls(
            numbers={c.key for c in claims if c.kind == "number"},
            dates=[c.key for c in claims if c.kind == "date"],
            text=_collapse(_fold(joined)),
        )

    def supports(self, claim: Claim) -> bool:
        if claim.kind == "number":
            return claim.key in self.numbers
        if claim.kind == "date":
            return any(
                all(g is None or g == s for g, s in zip(claim.key, src, strict=True)) for src in self.dates
            )
        if claim.kind == "person" and not claim.key.isascii():
            run = claim.key
            return any(run[i:] in self.text for i in range(len(run) - 1))
        if claim.kind == "person":
            # Initials ("J.") are skipped; every spelled-out part of the
            # name has to appear in the source.
            words = [w for w in claim.key.replace(".", " ").split() if len(w) > 1]
            return bool(words) and all(_text_pattern(w).search(self.text) for w in words)
        return _text_pattern(claim.key).search(self.text) is not None


# ---- policy and check ------------------------------------------------------


def _string_list(policy: dict[str, Any], key: str) -> list[str]:
    value = policy.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message=f"Policy key '{key}' must be a non-empty list of non-empty strings, got {value!r}.",
            remediation="grounding_sources lists the source files (paths); grounding_terms lists extra terms to check.",
            evidence={"key": key},
        )
    return list(value)


def validate_grounding_policy(policy: dict[str, Any]) -> None:
    """Raise ARTIFACT_INVALID_ARGS for a malformed grounding policy, so the
    mistake is reported before any work is done rather than at verify time."""
    if "grounding_terms" in policy and "grounding_sources" not in policy:
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message="Policy key 'grounding_terms' needs 'grounding_sources': there is nothing to check the terms against.",
            remediation="Add grounding_sources: the transcript, slides or other files the document was written from.",
            evidence={"key": "grounding_terms"},
        )
    if "grounding_sources" in policy:
        _string_list(policy, "grounding_sources")
    if "grounding_terms" in policy:
        _string_list(policy, "grounding_terms")


def _read_source(path_str: str) -> str:
    """The readable text of one source file. A format an adapter knows is
    read through that adapter (slides, PDF, DOCX, Markdown, ...); anything
    else (.txt, .srt, .vtt transcripts) is read as UTF-8 text."""
    from artifact_skill.adapters.registry import get_adapter
    from artifact_skill.core.artifact import ArtifactRef, ArtifactType
    from artifact_skill.security.paths import check_input_size

    path = Path(path_str)
    if not path.is_file():
        raise ValueError("file not found")
    ref = ArtifactRef.from_path(path)
    if ref.type == ArtifactType.UNKNOWN:
        check_input_size(path)
        try:
            return path.read_text(encoding="utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("not a format this tool reads, and not UTF-8 text") from exc
    text = get_adapter(ref.type).inspect(ref).text
    if text is None:
        raise ValueError(f"no text can be extracted from a '{ref.type.value}' file")
    return text


def _try_read_source(path_str: str) -> tuple[str | None, str]:
    try:
        return _read_source(path_str), ""
    except (ValueError, OSError, ArtifactError) as exc:
        return None, str(exc)


def grounding_check(adapter: ArtifactAdapter, ref: ArtifactRef, policy: dict[str, Any]) -> Check | None:
    """The `source_grounding` check, or None when the policy asks for none."""
    if "grounding_sources" not in policy:
        return None
    validate_grounding_policy(policy)
    sources = _string_list(policy, "grounding_sources")
    terms = _string_list(policy, "grounding_terms") if "grounding_terms" in policy else []

    try:
        text = adapter.inspect(ref).text
    except ArtifactError as exc:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"Could not read the document's text: {exc}", evidence={"sources": sources})
    if text is None:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"The '{adapter.id}' adapter does not extract text, so nothing could be compared.",
                     evidence={"sources": sources})
    if not text.strip():
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message="No readable text was found in the document (a scanned PDF has none), so nothing "
                     "could be compared.", evidence={"sources": sources})

    source_texts: list[str] = []
    unreadable: list[dict[str, str]] = []
    for src in sources:
        source_text, reason = _try_read_source(src)
        if source_text is None:
            unreadable.append({"source": src, "reason": reason})
        else:
            source_texts.append(source_text)
    if unreadable:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"{len(unreadable)} source file(s) could not be read, so the document was not compared.",
                     evidence={"sources": sources, "unreadable_sources": unreadable})

    claims = extract_claims(text, terms)
    index = _SourceIndex.build(source_texts, terms)
    unsupported = [c for c in claims if not index.supports(c)]
    evidence = {
        "sources": sources,
        "claims_checked": len(claims),
        "unsupported": [c.to_dict() for c in unsupported],
    }
    if unsupported:
        listed = ", ".join(f"{c.text!r} (line {c.lines[0]})" for c in unsupported[:10])
        more = f" and {len(unsupported) - 10} more" if len(unsupported) > 10 else ""
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.FAIL,
                     message=f"{len(unsupported)} of {len(claims)} claim(s) do not appear in the source: {listed}{more}. "
                     "Nothing was changed; check each one against the source.", evidence=evidence)
    return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.PASS,
                 message=f"All {len(claims)} number(s), date(s), name(s) and term(s) found appear in the source.",
                 evidence=evidence)


def verify_structural_with_grounding(
    adapter: ArtifactAdapter, ref: ArtifactRef, policy: dict[str, Any]
) -> VerificationResult:
    """`adapter.verify_structural()` plus the `source_grounding` check when
    the policy names sources. Every verify path (CLI, MCP, lifecycle) goes
    through this, so no adapter has to know about grounding."""
    result = adapter.verify_structural(ref, policy)
    check = grounding_check(adapter, ref, policy)
    if check is not None:
        result.checks.append(check)
    return result
