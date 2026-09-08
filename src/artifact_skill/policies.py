"""Named policy presets (Issue #14).

The default policy `{}` barely gates anything — with no policy, roughly
the only thing that reliably fails is a structural defect like zero pages
(see `docs/verification.md`). An agent that goes straight from `execute`
to `receipt` without ever setting a policy — the easy, low-friction path —
gets almost no real submission gate. These presets are starting points an
agent can reach for instead of assembling a policy dict field-by-field.

This is deliberately *not* a new engine concept: a preset is just a plain
policy dict, the same shape `verify_structural(ref, policy)` always took.
`core/engine.py` still only ever applies whatever policy dict it's given —
nothing here changes that contract. A preset dict is safe to reuse across
adapters it wasn't written for: an adapter that doesn't recognize a given
policy key simply never looks it up (see each adapter's `verify_structural()`
— every policy read is a `policy.get(...)`/`"key" in policy` check, never
an assumption that every key is meaningful), so e.g. `web-no-external`'s
`require_title` key is inert (harmlessly ignored) when applied to an SVG
file, which has no such concept.
"""

from __future__ import annotations

from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "print-a4": {
        "require_page_size_pt": (595, 842),
        "page_size_tolerance_pt": 2.0,
        "forbid_unembedded_fonts": True,
        "require_no_encryption": True,
        "forbid_blank_pages": True,
        "min_pages": 1,
        "forbid_placeholder_text": True,
    },
    "print-letter": {
        "require_page_size_pt": (612, 792),
        "page_size_tolerance_pt": 2.0,
        "forbid_unembedded_fonts": True,
        "require_no_encryption": True,
        "forbid_blank_pages": True,
        "min_pages": 1,
        "forbid_placeholder_text": True,
    },
    "slides-16x9": {
        "require_slide_aspect_ratio": 16 / 9,
        "aspect_ratio_tolerance": 0.02,
        "min_slides": 1,
        "forbid_placeholder_text": True,
        # max_empty_placeholders isn't set here: PPTX's own default (0) is
        # already the strict behavior this preset wants.
    },
    "spreadsheet-no-errors": {
        "forbid_external_links": True,
        "forbid_placeholder_text": True,
        # formula_cached_errors already FAILs on a cached error under the
        # default policy ({}) too - nothing to strengthen there. This
        # preset exists so an agent has a named thing to reach for, not
        # because {} was silently permissive for this specific check.
    },
    "web-no-external": {
        "forbid_external_resources": True,
        "require_title": True,  # HTML only; inert (harmlessly ignored) for SVG
        "forbid_placeholder_text": True,
    },
}


def resolve_policy(preset_name: str | None, explicit_policy: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a named preset (if any) with an explicit policy dict (if any).

    `explicit_policy`'s keys win on conflict — a caller naming a preset can
    still override individual fields rather than being stuck with the
    preset verbatim. Raises `KeyError` naming the valid preset names if
    `preset_name` doesn't match one, rather than silently applying no
    policy at all for a typo'd name.
    """
    base: dict[str, Any] = {}
    if preset_name:
        if preset_name not in PRESETS:
            raise KeyError(f"Unknown policy preset '{preset_name}'. Valid presets: {sorted(PRESETS)}.")
        base = dict(PRESETS[preset_name])
    if explicit_policy:
        base.update(explicit_policy)
    return base
