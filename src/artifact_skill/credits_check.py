"""Credits and licence lines checked against the licence records of the
assets used (ecosystem roadmap step 8).

An end roll, a video description or a credits document has to carry the
attribution every asset's licence asks for: BGM and sound effects
(sound-design-skill's library records), stock footage and photos, AI
voices, fonts. With `policy["licence_records"]` set, this module reads one
record per asset and checks the document's text:

  - a record with an `attribution` line (or, failing that, a `credit`
    line) needs that line in the document, as written; whitespace, letter
    case, quote style and full-width forms do not count as differences;
  - a Creative Commons licence with the BY element (CC BY, CC BY-SA,
    CC BY-NC, ...) and no declared line needs the declared author and
    title, and the licence named with its version ("CC BY 4.0",
    "CC-BY-4.0", "Creative Commons Attribution 4.0") or its URL;
  - a licence that asks for no attribution (CC0, public domain, a font
    under the SIL Open Font Licence, a paid stock licence with no credit
    recorded) needs nothing;
  - a record with no licence, or "unknown", cannot be checked and is
    reported as such.

The record shape is the one sound-design-skill writes for its library
(`licence`, `attribution`, `credit`, `title`, `author`, `source`,
`licence_url`), named by `asset` (or `file`); a library-index row, whose
`licence` is an object holding those fields, is read as well. Nothing in
the document is changed and no missing line is written for the caller:
each finding names the asset and what its licence asks for.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from artifact_skill.core.errors import ArtifactError, ArtifactInputError
from artifact_skill.core.verification import Check, CheckStatus

if TYPE_CHECKING:
    from artifact_skill.adapters.base import ArtifactAdapter
    from artifact_skill.core.artifact import ArtifactRef

CREDITS_POLICY_KEYS = frozenset({"licence_records"})
CHECK_ID = "credits_attribution"
CHECK_NAME = "Every attribution the assets' licences ask for is in the document"

_RECORD_FIELDS = ("licence", "attribution", "credit", "title", "author", "source", "licence_url")
_UNKNOWN_WORDS = frozenset({"", "unknown", "none", "n/a", "na", "tbd", "?", "-"})
_MAX_RECORDS_FILE_BYTES = 10 * 1024 * 1024

# ---- Creative Commons licence names ---------------------------------------

_CC_ELEMENT_WORDS = {
    "by": "by", "attribution": "by", "sa": "sa", "sharealike": "sa", "share-alike": "sa",
    "nc": "nc", "noncommercial": "nc", "non-commercial": "nc", "nd": "nd", "noderivatives": "nd",
    "noderivs": "nd", "no-derivatives": "nd",
}
_CC_SHORT_RE = re.compile(
    r"(?<![a-z])cc[\s-]?(?P<els>(?:by|0|zero)(?:[\s-]?(?:sa|nc|nd))*)(?:[\s-]?(?P<ver>\d\.\d))?(?![a-z0-9])",
    re.IGNORECASE,
)
_CC_LONG_RE = re.compile(
    r"creative\s+commons\s+(?P<els>(?:attribution|zero)(?:[\s-]+(?:non-?commercial|share-?alike|no-?deriv(?:ative)?s))*)"
    r"(?:\s+(?P<ver>\d\.\d))?",
    re.IGNORECASE,
)
_ATTRIBUTION_WORD_RE = re.compile(r"\battribution\b|\bcc[\s-]?by\b", re.IGNORECASE)


def _cc_elements(raw: str) -> frozenset[str]:
    words = re.split(r"[\s-]+", raw.lower())
    if words and words[0] in {"0", "zero"}:
        return frozenset({"zero"})
    # "non-commercial", "share-alike" and "no-derivatives" were split on
    # their hyphens; join them back before mapping.
    joined = re.sub(r"non\s+commercial", "noncommercial", " ".join(words))
    joined = re.sub(r"share\s+alike", "sharealike", joined)
    joined = re.sub(r"no\s+deriv", "noderiv", joined)
    return frozenset(_CC_ELEMENT_WORDS[w] for w in joined.split() if w in _CC_ELEMENT_WORDS)


def parse_cc(text: str) -> list[tuple[frozenset[str], str | None]]:
    """Every Creative Commons licence named in `text`, as (elements,
    version): ("CC BY-SA 4.0" -> ({"by", "sa"}, "4.0"))."""
    found: list[tuple[frozenset[str], str | None]] = []
    folded = _fold(text)
    for pattern in (_CC_SHORT_RE, _CC_LONG_RE):
        for m in pattern.finditer(folded):
            elements = _cc_elements(m.group("els"))
            if elements:
                found.append((elements, m.group("ver")))
    return found


# ---- text matching ---------------------------------------------------------

_QUOTES = str.maketrans({
    chr(0x201C): '"', chr(0x201D): '"', chr(0x201E): '"', chr(0x2018): "'", chr(0x2019): "'",
    chr(0x00AB): '"', chr(0x00BB): '"', chr(0x300C): '"', chr(0x300D): '"',
})


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_QUOTES)


def _comparable(text: str) -> str:
    return re.sub(r"\s+", " ", _fold(text)).strip().casefold()


def _contains(document: str, needle: str) -> bool:
    return _comparable(needle) in document


# ---- records ---------------------------------------------------------------


def _text_field(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if key == "licence" and value is None:
        value = record.get("license")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalise_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a sound-design-skill library-index row, whose `licence` is
    an object holding the licence fields, into the flat record shape."""
    record = dict(raw)
    nested = record.get("licence", record.get("license"))
    if isinstance(nested, dict):
        record.pop("license", None)
        record["licence"] = nested.get("licence", nested.get("license"))
        for key in _RECORD_FIELDS[1:]:
            if record.get(key) is None and nested.get(key) is not None:
                record[key] = nested[key]
    return record


def _asset_name(record: dict[str, Any]) -> str | None:
    for key in ("asset", "file", "title"):
        value = _text_field(record, key)
        if value:
            return value
    return None


def _invalid(message: str) -> ArtifactInputError:
    return ArtifactInputError(
        code="ARTIFACT_INVALID_ARGS",
        message=message,
        remediation="licence_records is a list of asset records ({asset, licence, attribution, credit, author, "
        "title, licence_url}) or the path of a JSON file holding one. See docs/verification.md.",
        evidence={"key": "licence_records"},
    )


def _check_record_list(records: Any, where: str) -> list[dict[str, Any]]:
    if not isinstance(records, list) or not records:
        raise _invalid(f"licence_records {where} must be a non-empty list of asset records.")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(records):
        if not isinstance(raw, dict):
            raise _invalid(f"licence_records {where}: entry {i} is not an object.")
        record = _normalise_record(raw)
        if _asset_name(record) is None:
            raise _invalid(f"licence_records {where}: entry {i} names no asset (asset, file or title).")
        out.append(record)
    return out


def validate_credits_policy(policy: dict[str, Any]) -> None:
    """Reject a malformed licence_records value before any work. A path is
    only checked for its type here; reading it happens at verify time."""
    if "licence_records" not in policy:
        return
    value = policy["licence_records"]
    if isinstance(value, str):
        if not value.strip():
            raise _invalid("licence_records must not be an empty path.")
        return
    _check_record_list(value, "in the policy")


def load_records(value: Any) -> list[dict[str, Any]]:
    """The asset records, from the policy value itself or from the JSON file
    it names (a list, or an object holding the list under `assets`,
    `files` or `records`)."""
    if not isinstance(value, str):
        return _check_record_list(value, "in the policy")
    path = Path(value)
    if not path.is_file():
        raise ValueError(f"licence records file not found: {value}")
    if path.stat().st_size > _MAX_RECORDS_FILE_BYTES:
        raise ValueError(f"licence records file is larger than {_MAX_RECORDS_FILE_BYTES} bytes: {value}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"licence records file is not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        data = next((data[k] for k in ("assets", "files", "records") if isinstance(data.get(k), list)), data)
    try:
        return _check_record_list(data, f"in {value}")
    except ArtifactInputError as exc:
        raise ValueError(exc.message) from exc


# ---- the check -------------------------------------------------------------


def _evaluate(record: dict[str, Any], document: str, cc_in_document: list[tuple[frozenset[str], str | None]]
              ) -> dict[str, Any] | None:
    """None when the record's licence is satisfied (or asks for nothing),
    else a finding."""
    asset = _asset_name(record)
    licence = _text_field(record, "licence")
    finding: dict[str, Any] = {"asset": asset, "licence": licence}
    if licence is None or licence.casefold() in _UNKNOWN_WORDS:
        return {**finding, "kind": "licence_unknown",
                "reason": "No licence is recorded for this asset, so what it asks for cannot be checked."}

    required = _text_field(record, "attribution") or _text_field(record, "credit")
    record_cc = parse_cc(licence)
    cc_by = next((cc for cc in record_cc if "by" in cc[0]), None)
    asks_for_attribution = required is not None or cc_by is not None or bool(_ATTRIBUTION_WORD_RE.search(licence))
    if not asks_for_attribution:
        return None

    author = _text_field(record, "author")
    title = _text_field(record, "title")
    if required is not None:
        if _contains(document, required):
            return None
        partial = [part for part in (author, title) if part and _contains(document, part)]
        if partial:
            return {**finding, "kind": "worded_differently", "expected": required,
                    "reason": "The asset is credited, but not with the line its licence record asks for."}
        return {**finding, "kind": "missing", "expected": required,
                "reason": "The attribution line this asset's licence asks for is not in the document."}

    if author is None and title is None:
        return {**finding, "kind": "record_incomplete",
                "reason": "The licence asks for attribution, but the record has no attribution line, author or "
                "title to look for."}
    missing: list[str] = [name for name, part in (("author", author), ("title", title))
                          if part and not _contains(document, part)]
    licence_url = _text_field(record, "licence_url")
    named = cc_by is not None and any(
        elements == cc_by[0] and (cc_by[1] is None or version == cc_by[1]) for elements, version in cc_in_document
    )
    if cc_by is None:
        named = _contains(document, licence)
    if not named and not (licence_url and _contains(document, licence_url)):
        missing.append("licence")
    if not missing:
        return None
    present = [p for p in ("author", "title") if p not in missing and _text_field(record, p)]
    kind = "missing" if not present else "incomplete"
    return {**finding, "kind": kind, "missing_parts": missing,
            "reason": f"The licence asks for attribution; not in the document: {', '.join(missing)}."}


def credits_check(adapter: ArtifactAdapter, ref: ArtifactRef, policy: dict[str, Any]) -> Check | None:
    """The `credits_attribution` check, or None when the policy asks for none."""
    if "licence_records" not in policy:
        return None
    validate_credits_policy(policy)
    try:
        records = load_records(policy["licence_records"])
    except (ValueError, OSError) as exc:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"The licence records could not be read: {exc}", evidence={})
    try:
        text = adapter.inspect(ref).text
    except ArtifactError as exc:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"Could not read the document's text: {exc}", evidence={"assets": len(records)})
    if text is None or not text.strip():
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message="The document has no readable text, so its credits could not be checked.",
                     evidence={"assets": len(records)})

    document = _comparable(text)
    cc_in_document = parse_cc(text)
    findings = [f for f in (_evaluate(r, document, cc_in_document) for r in records) if f is not None]
    failures = [f for f in findings if f["kind"] in {"missing", "incomplete", "worded_differently"}]
    unknown = [f for f in findings if f not in failures]
    evidence = {"assets": len(records), "findings": findings}
    if failures:
        listed = "; ".join(f"{f['asset']} ({f['licence']}): {f['kind'].replace('_', ' ')}" for f in failures[:10])
        more = f"; and {len(failures) - 10} more" if len(failures) > 10 else ""
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.FAIL,
                     message=f"{len(failures)} of {len(records)} asset(s) lack the attribution their licence asks for: "
                     f"{listed}{more}. Nothing was changed.", evidence=evidence)
    if unknown:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"{len(unknown)} asset record(s) could not be checked: "
                     + "; ".join(f"{f['asset']}: {f['kind'].replace('_', ' ')}" for f in unknown[:10]) + ".",
                     evidence=evidence)
    return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.PASS,
                 message=f"Every attribution the {len(records)} asset(s)' licences ask for is in the document.",
                 evidence=evidence)
