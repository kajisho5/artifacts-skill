"""Event documents checked against the organiser's source list (ecosystem
roadmap step 6).

A cue sheet, run-of-show sheet, speaker list or lower-third CSV is
generated from the organiser's list of speakers and sessions. A name
spelled with the wrong variant of a character, a dropped honorific or a
session time that drifted is the error that hurts most on the day. With
`policy["event_source"]` set, this module reads the source list and, for
each of its rows, checks the document's text:

  - the row's `name` appears exactly as written in the source. Only
    whitespace between the characters of a CJK name and full-width ASCII
    letters and digits count as no difference; a variant character does (髙/高,
    﨑/崎, 邊/辺, 齋/斎, ...), so a name found only in a variant spelling
    is reported as `variant_spelling` with the character that differs,
    and a name not found at all as `missing`;
  - the row's `honorific`, when it has one (先生, 様, Dr., Prof.), stands
    next to the name (`honorific_dropped` otherwise);
  - the row's `title` (role) and `affiliation` appear exactly as written,
    on the name's line or the lines around it (`variant_spelling` or
    `mismatch` otherwise);
  - the row's `start` and `end` times appear on the name's line (or, when
    that line carries no time at all, on the lines around it): a line
    with other times is a `time_mismatch`.

A row with no name is anchored on its `session` title instead. Every
finding carries the source row (its number in the source and its fields)
and the document line it was found on. Nothing is changed: the check
reports, the caller fixes.

The source is a CSV file with a header row, a JSON file (a list of rows,
or an object holding one under `rows`, `speakers`, `sessions` or
`entries`), or the list itself. Column names are matched case-insensitively
and a few common alternatives are accepted (`speaker`, `affiliation`/
`organisation`, `start_time`, ...); Japanese headers (氏名, 所属, 役職,
敬称, 開始, 終了, ...) are read as well, as an opt-in convenience for
Japanese organisers' lists.
"""

from __future__ import annotations

import csv
import io
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

EVENT_POLICY_KEYS = frozenset({"event_source", "event_fields"})
CHECK_ID = "event_source_match"
CHECK_NAME = "Names, titles, affiliations and times match the organiser's source list"

FIELDS = ("name", "honorific", "title", "affiliation", "session", "start", "end")
_MAX_SOURCE_BYTES = 10 * 1024 * 1024
# How many lines before and after the name's line belong to the same entry
# (a speaker list that puts the affiliation on the next line, a DOCX table
# whose cells are one line each).
_WINDOW_BEFORE = 1
_WINDOW_AFTER = 3

_HEADER_ALIASES: dict[str, str] = {
    "name": "name", "speaker": "name", "speaker_name": "name", "presenter": "name", "full_name": "name",
    "氏名": "name", "名前": "name", "講演者": "name", "演者": "name", "登壇者": "name",
    "honorific": "honorific", "salutation": "honorific", "敬称": "honorific",
    "title": "title", "role": "title", "position": "title", "job_title": "title", "役職": "title", "肩書": "title",
    "肩書き": "title",
    "affiliation": "affiliation", "organisation": "affiliation", "organization": "affiliation",
    "institution": "affiliation", "company": "affiliation", "所属": "affiliation", "所属先": "affiliation",
    "session": "session", "session_title": "session", "talk": "session", "talk_title": "session",
    "program": "session", "programme": "session", "演題": "session", "セッション": "session", "プログラム": "session",
    "start": "start", "start_time": "start", "begins": "start", "from": "start", "開始": "start",
    "開始時刻": "start", "開始時間": "start",
    "end": "end", "end_time": "end", "ends": "end", "to": "end", "終了": "end", "終了時刻": "end", "終了時間": "end",
}

# Characters that are different code points for what a reader may take to
# be the same character. A document that writes the other one has not
# spelled the name as the source does. Characters whose NFKC form is
# another character (the CJK compatibility ideographs: 﨑 -> 崎) are
# covered without being listed.
_VARIANT_GROUPS = (
    "高髙", "崎﨑嵜碕", "辺邊邉", "斎齋斉齊", "沢澤", "浜濱", "国國", "徳德", "吉𠮷", "富冨", "島嶋嶌",
    "桜櫻", "竜龍", "恵惠", "広廣", "実實", "栄榮", "槙槇", "柳栁", "瀬瀨", "黒黑", "曽曾", "條条",
    "淵渕渊", "鉄鐵", "関關", "峰峯", "舘館", "穂穗", "壮壯", "真眞", "円圓", "会會", "学學",
)


def _variant_map() -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for group in _VARIANT_GROUPS:
        chars = set(group)
        for c in group:
            out.setdefault(c, set()).update(chars)
    return {c: frozenset(v) for c, v in out.items()}


_VARIANTS = _variant_map()

# ---- text folding ----------------------------------------------------------

_FULLWIDTH_ASCII = {cp: cp - 0xFEE0 for cp in range(0xFF01, 0xFF5F)}
_FULLWIDTH_ASCII[0x3000] = 0x20


def _fold(text: str) -> str:
    """Full-width ASCII to ASCII and the ideographic space to a space;
    deliberately not NFKC, which would turn 﨑 into 崎 and hide the very
    difference this check looks for."""
    return text.translate(_FULLWIDTH_ASCII)


def _is_cjk(ch: str) -> bool:
    name = unicodedata.name(ch, "")
    return name.startswith(("CJK", "HIRAGANA", "KATAKANA")) or "IDEOGRAPH" in name


def _char_class(ch: str, variants: bool) -> str:
    if not variants:
        return re.escape(ch)
    alts = set(_VARIANTS.get(ch, ()))
    alts.add(ch)
    nfkc = unicodedata.normalize("NFKC", ch)
    alts.add(nfkc)
    alts.update(_VARIANTS.get(nfkc, ()))
    if len(alts) == 1:
        return re.escape(ch)
    return "(?:" + "|".join(re.escape(a) for a in sorted(alts)) + ")"


def _pattern(value: str, variants: bool = False, whole: bool = False) -> re.Pattern[str]:
    """`value` as a regex. Whitespace between two Latin letters or digits
    matches any whitespace but must be there ("Van Dyke" is not "VanDyke");
    next to a CJK character it may be there or not (山田太郎 == 山田 太郎). A
    letter or digit right before or after a Latin end does not count as a
    match; with `whole` (titles, affiliations, sessions) no letter, digit or
    CJK character may stand right before or after it either, so "教授" is
    not found in "准教授"."""
    chars = list(_fold(value).strip())
    parts: list[str] = []
    for i, ch in enumerate(chars):
        if ch.isspace():
            if i > 0 and chars[i - 1].isspace():
                continue
            prev = chars[i - 1] if i > 0 else ""
            nxt = next((c for c in chars[i + 1:] if not c.isspace()), "")
            parts.append(r"\s*" if _is_cjk(prev) or _is_cjk(nxt) else r"\s+")
            continue
        parts.append(_char_class(ch, variants))
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if nxt and not nxt.isspace() and (_is_cjk(ch) or _is_cjk(nxt)):
            parts.append(r"\s*")
    body = "".join(parts)
    first, last = chars[0], chars[-1]
    if whole:
        before = r"(?<![^\W_])" if first.isalnum() else ""
        after = r"(?![^\W_])" if last.isalnum() else ""
    else:
        before = r"(?<![^\W_])" if first.isalnum() and not _is_cjk(first) else ""
        after = r"(?![^\W_])" if last.isalnum() and not _is_cjk(last) else ""
    return re.compile(before + body + after)


def _differing_chars(expected: str, found: str) -> list[dict[str, str]]:
    exp = [c for c in _fold(expected) if not c.isspace()]
    got = [c for c in found if not c.isspace()]
    return [{"expected": e, "found": g} for e, g in zip(exp, got, strict=False) if e != g]


# ---- times -----------------------------------------------------------------

_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*(?::|時)\s*([0-5]\d)(?:\s*分)?(?!\d)")


def _time_key(value: str) -> str | None:
    m = _TIME_RE.search(unicodedata.normalize("NFKC", value))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _times_in(line: str) -> list[str]:
    return [f"{int(m.group(1)):02d}:{m.group(2)}" for m in _TIME_RE.finditer(unicodedata.normalize("NFKC", line))]


# ---- the source list -------------------------------------------------------


def _invalid(message: str) -> ArtifactInputError:
    return ArtifactInputError(
        code="ARTIFACT_INVALID_ARGS",
        message=message,
        remediation="event_source is the organiser's list (a CSV or JSON file, or a list of rows with name, "
        "honorific, title, affiliation, session, start, end); event_fields optionally lists which of those fields "
        "the document must carry. See docs/verification.md.",
        evidence={"key": "event_source"},
    )


def _canonical_key(key: str) -> str | None:
    k = unicodedata.normalize("NFKC", str(key)).strip().casefold().replace(" ", "_").replace("-", "_")
    return _HEADER_ALIASES.get(k)


def _normalise_row(raw: dict[str, Any]) -> dict[str, str]:
    row: dict[str, str] = {}
    for key, value in raw.items():
        field = _canonical_key(key)
        if field is None or field in row or value is None:
            continue
        text = str(value).strip()
        if text:
            row[field] = text
    return row


def _check_rows(rows: Any, where: str) -> list[dict[str, str]]:
    if not isinstance(rows, list) or not rows:
        raise _invalid(f"event_source {where} must be a non-empty list of rows.")
    out: list[dict[str, str]] = []
    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise _invalid(f"event_source {where}: row {i + 1} is not an object.")
        row = _normalise_row(raw)
        if "name" not in row and "session" not in row:
            raise _invalid(f"event_source {where}: row {i + 1} has neither a name nor a session title.")
        for key in ("start", "end"):
            if key in row and _time_key(row[key]) is None:
                raise _invalid(f"event_source {where}: row {i + 1} has a {key} time that is not HH:MM: {row[key]!r}.")
        out.append(row)
    return out


def validate_event_policy(policy: dict[str, Any]) -> None:
    """Reject a malformed event_source or event_fields value before any
    work. A path is only checked for its type here."""
    if "event_fields" in policy and "event_source" not in policy:
        raise _invalid("Policy key 'event_fields' needs 'event_source': there is no source list to check against.")
    if "event_fields" in policy:
        fields = policy["event_fields"]
        if (not isinstance(fields, list) or not fields
                or not all(isinstance(f, str) and f in FIELDS for f in fields)):
            raise _invalid(f"event_fields must be a non-empty list drawn from {list(FIELDS)}, got {fields!r}.")
    if "event_source" not in policy:
        return
    value = policy["event_source"]
    if isinstance(value, str):
        if not value.strip():
            raise _invalid("event_source must not be an empty path.")
        return
    _check_rows(value, "in the policy")


def load_source(value: Any) -> list[dict[str, str]]:
    """The source rows, from the policy value itself or from the CSV or
    JSON file it names."""
    if not isinstance(value, str):
        return _check_rows(value, "in the policy")
    path = Path(value)
    if not path.is_file():
        raise ValueError(f"event source file not found: {value}")
    if path.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError(f"event source file is larger than {_MAX_SOURCE_BYTES} bytes: {value}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"event source file is not UTF-8 text: {exc}") from exc
    data: Any
    if path.suffix.lower() == ".json" or text.lstrip().startswith(("[", "{")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"event source file is not valid JSON: {exc}") from exc
        if isinstance(data, dict):
            data = next((data[k] for k in ("rows", "speakers", "sessions", "entries") if isinstance(data.get(k), list)),
                        data)
    else:
        try:
            dialect: Any = csv.Sniffer().sniff("\n".join(text.splitlines()[:20]), delimiters=",\t;")
        except csv.Error:
            dialect = csv.get_dialect("excel")
        data = [r for r in csv.DictReader(io.StringIO(text), dialect=dialect) if any((v or "").strip()
                                                                                     for v in r.values() if
                                                                                     isinstance(v, str))]
    try:
        return _check_rows(data, f"in {value}")
    except ArtifactInputError as exc:
        raise ValueError(exc.message) from exc


# ---- the document ----------------------------------------------------------


def document_lines(adapter: ArtifactAdapter, ref: ArtifactRef) -> tuple[list[tuple[int, str]], bool] | None:
    """The document's non-blank lines, each with its 1-based line number.
    A CSV's InspectionReport text has one cell per line; here each CSV row
    is one line, its cells joined by " | ", so a row's name and time stay
    on the same line. Blank lines are left out so that "the lines around
    the name" means the neighbouring entries' text, not empty lines. The
    flag is True when each line is one whole entry (a CSV row), so nothing
    is looked for on the lines around it."""
    from artifact_skill.core.artifact import ArtifactType

    raw: list[str]
    if ref.type == ArtifactType.CSV:
        from artifact_skill.adapters.csv.adapter import _read_rows

        try:
            rows, _ = _read_rows(Path(ref.path))
        except (UnicodeDecodeError, OSError, csv.Error):
            return None
        raw = [" | ".join(cell.strip() for cell in row) for row in rows]
        return [(n, line) for n, line in enumerate(raw, start=1) if line.strip()], True
    else:
        text = adapter.inspect(ref).text
        if text is None:
            return None
        raw = text.splitlines()
    return [(n, line) for n, line in enumerate(raw, start=1) if line.strip()], False


def _find(pattern: re.Pattern[str], lines: list[str], indices: range | list[int] | None = None
          ) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for i in indices if indices is not None else range(len(lines)):
        m = pattern.search(_fold(lines[i]))
        if m:
            out.append((i, m.group(0)))
    return out


def _window(i: int, n: int, row_per_line: bool = False) -> list[int]:
    if row_per_line:
        return [i]
    return list(range(max(0, i - _WINDOW_BEFORE), min(n, i + _WINDOW_AFTER + 1)))


def _has_honorific(line: str, matched: str, honorific: str) -> bool:
    folded = _fold(line)
    hon = _fold(honorific).strip()
    pos = folded.find(matched)
    if pos < 0:
        return False
    # a CSV row's cells are joined by " | ": the honorific may be the
    # next (or previous) cell, but no further
    after = re.sub(r"^\s*(?:\|\s*)?", "", folded[pos + len(matched):])
    before = re.sub(r"(?:\s*\|)?\s*$", "", folded[:pos])
    return after.startswith(hon) or before.endswith(hon)


def _times_match(times: list[str], start: str | None, end: str | None) -> bool:
    """`start` and `end` among `times` in their roles: the end right after
    the start (a "09:30 | 10:20" row, a "09:30-10:20" range), so exchanged
    times do not match."""
    if start is None:
        return end in times
    for j, t in enumerate(times):
        if t == start and (end is None or (j + 1 < len(times) and times[j + 1] == end)):
            return True
    return False


def _row_findings(row_no: int, row: dict[str, str], fields: set[str], numbered: list[tuple[int, str]],
                  row_per_line: bool = False) -> list[dict[str, Any]]:
    lines = [text for _, text in numbered]

    def window(i: int) -> list[int]:
        return _window(i, len(lines), row_per_line)

    findings: list[dict[str, Any]] = []
    source = {"row": row_no, **row}

    def finding(kind: str, field: str, reason: str, line_index: int | None = None, **extra: Any) -> None:
        item: dict[str, Any] = {"kind": kind, "field": field, "expected": row.get(field), "source_row": source,
                                "reason": reason}
        if line_index is not None:
            item["line"] = numbered[line_index][0]
            item["line_text"] = lines[line_index].strip()
        item.update(extra)
        findings.append(item)

    anchor_field = "name" if "name" in row else "session"
    anchor = row[anchor_field]
    exact = _find(_pattern(anchor, whole=anchor_field != "name"), lines)
    if not exact and anchor_field not in fields and anchor_field == "name" and "session" in row:
        # the name is not one of the fields to check: find the entry by its
        # session instead
        anchor_field, anchor = "session", row["session"]
        exact = _find(_pattern(anchor, whole=True), lines)
    anchors = [i for i, _ in exact]
    matched_text = dict(exact)
    if not exact and anchor_field not in fields:
        finding("entry_not_found", anchor_field,
                f"The entry could not be found in the document by its {anchor_field}, so its other fields could "
                "not be checked.")
        return findings
    if not exact:
        variant = _find(_pattern(anchor, variants=True), lines)
        if variant:
            i, found = variant[0]
            finding("variant_spelling", anchor_field,
                    "Found only with a different character from the source's spelling.", i,
                    found=found, differs=_differing_chars(anchor, found))
            anchors = [j for j, _ in variant]
            matched_text = dict(variant)
        else:
            finding("missing", anchor_field, f"The {anchor_field} in the source list is not in the document.")
            return findings
    if not anchors:
        return findings

    # When the source row has a session too, prefer the occurrences of the
    # name that sit next to that session (a speaker with two talks).
    if anchor_field == "name" and "session" in row:
        session_pat = _pattern(row["session"], whole=True)
        near = [i for i in anchors if _find(session_pat, lines, window(i))]
        if near:
            anchors = near

    if "honorific" in row and "honorific" in fields:
        if not any(_has_honorific(lines[i], matched_text[i], row["honorific"]) for i in anchors):
            finding("honorific_dropped", "honorific", "The name appears without the honorific the source gives.",
                    anchors[0])

    for field in ("title", "affiliation", "session"):
        if field == anchor_field or field not in row or field not in fields:
            continue
        pat = _pattern(row[field], whole=True)
        if any(_find(pat, lines, window(i)) for i in anchors):
            continue
        variant_pat = _pattern(row[field], variants=True, whole=True)
        hit = next((h for i in anchors for h in _find(variant_pat, lines, window(i))), None)
        if hit is not None:
            finding("variant_spelling", field, "Found only with a different character from the source's spelling.",
                    hit[0], found=hit[1], differs=_differing_chars(row[field], hit[1]))
        else:
            finding("mismatch", field, f"The {field} in the source list is not next to this entry in the document.",
                    anchors[0])

    expected = [(f, _time_key(row[f])) for f in ("start", "end") if f in row and f in fields]
    expected_times = [(f, t) for f, t in expected if t is not None]
    if expected_times:
        candidates: list[tuple[int, list[str]]] = []
        for i in anchors:
            on_line = _times_in(lines[i])
            if on_line:
                candidates.append((i, on_line))
            else:
                around = [t for j in window(i) for t in _times_in(lines[j])]
                if around:
                    candidates.append((i, around))
        want = dict(expected_times)
        if not candidates:
            finding("time_missing", expected_times[0][0],
                    "The source list gives a time for this entry, but the document gives none near it.", anchors[0],
                    expected=want)
        elif not any(_times_match(times, want.get("start"), want.get("end")) for _, times in candidates):
            i, times = candidates[0]
            # the first time given for the entry is its start
            wrong = "start" if "start" in want and times[0] != want["start"] else ("end" if "end" in want else "start")
            finding("time_mismatch", wrong,
                    "The document gives a different time for this entry than the source list.", i,
                    expected=want, found=times)
    return findings


def event_check(adapter: ArtifactAdapter, ref: ArtifactRef, policy: dict[str, Any]) -> Check | None:
    """The `event_source_match` check, or None when the policy asks for none."""
    if "event_source" not in policy:
        return None
    validate_event_policy(policy)
    fields = set(policy.get("event_fields") or FIELDS)
    try:
        rows = load_source(policy["event_source"])
    except (ValueError, OSError) as exc:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"The event source list could not be read: {exc}", evidence={})
    try:
        doc = document_lines(adapter, ref)
    except ArtifactError as exc:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message=f"Could not read the document's text: {exc}", evidence={"rows": len(rows)})
    if doc is None or not doc[0]:
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.UNKNOWN,
                     message="The document has no readable text, so it could not be compared with the source list.",
                     evidence={"rows": len(rows)})

    findings = [f for n, row in enumerate(rows, start=1) for f in _row_findings(n, row, fields, doc[0], doc[1])]
    evidence = {"rows": len(rows), "findings": findings}
    if findings:
        listed = "; ".join(
            f"row {f['source_row']['row']} {f['field']}: {f['kind'].replace('_', ' ')}"
            + (f" ({f['found']!r} for {f['expected']!r})" if f["kind"] == "variant_spelling" else "")
            for f in findings[:10]
        )
        more = f"; and {len(findings) - 10} more" if len(findings) > 10 else ""
        return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.FAIL,
                     message=f"{len(findings)} difference(s) from the source list: {listed}{more}. Nothing was changed.",
                     evidence=evidence)
    return Check(id=CHECK_ID, name=CHECK_NAME, status=CheckStatus.PASS,
                 message=f"Every one of the {len(rows)} source row(s) is in the document as the source writes it.",
                 evidence=evidence)
