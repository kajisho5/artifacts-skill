"""Per-run default evidence directories (Issue #47).

When a CLI/MCP caller omits `--evidence-dir` / `--out-dir`, every
invocation used to share the fixed, cwd-relative `./reports` directory.
`render()` writes fixed filenames (`rendered/page-001.png`,
`rendered/frame-001.png`, ...) and the receipt is always `receipt.json`,
so two concurrent runs overwrote each other's evidence: a receipt could
point at a PNG a different, concurrent invocation had written for a
different input. Reproduced with 50 concurrent `receipt` subprocesses.

The fix: a caller that omits the directory now gets a fresh directory
of its own under `./reports`, reserved with a single `mkdir()` call that
fails if the name already exists. `mkdir()` is atomic on every local
filesystem, so two processes can never be handed the same directory, even
in the rare case that both pick the same random name (the loser just
picks again). Inside that directory the layout is unchanged
(`receipt.json`, `rendered/...`, `contact-sheet.png`, ...). An explicit
directory is used exactly as given, as before, so callers that need a
known, reusable location still pass one.

See docs/architecture.md ("Evidence directory per run").
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EVIDENCE_ROOT = Path("reports")

# Bound on name clashes before giving up. Each attempt draws 32 fresh random
# bits, so even one retry is already very unlikely; this only guards against
# looping forever on a root that rejects every mkdir() for some other reason.
_MAX_ATTEMPTS = 100


def new_run_id() -> str:
    """`<UTC timestamp>-<8 hex chars>`, e.g. `20261030T120501Z-3f9a0c1b`.

    The timestamp makes run directories sort in the order they were made;
    the random suffix is what keeps two runs in the same second apart.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def new_run_dir(root: Path = DEFAULT_EVIDENCE_ROOT, *, create: bool = True) -> Path:
    """Return a directory under `root` that no other run is using.

    With `create=True` the directory is reserved by `mkdir(exist_ok=False)`
    before this returns, so the name belongs to this run alone. With
    `create=False` (dry runs, which must write nothing) only a fresh name is
    returned; nothing is created.
    """
    if not create:
        return root / new_run_id()
    root.mkdir(parents=True, exist_ok=True)
    last_error: FileExistsError | None = None
    for _ in range(_MAX_ATTEMPTS):
        candidate = root / new_run_id()
        try:
            candidate.mkdir()
        except FileExistsError as exc:
            last_error = exc
            continue
        return candidate
    raise RuntimeError(f"could not reserve a unique run directory under '{root}'") from last_error


@contextmanager
def evidence_dir_for(
    explicit: str | Path | None, *, dry_run: bool = False, root: Path = DEFAULT_EVIDENCE_ROOT,
) -> Iterator[Path]:
    """Yield the directory a CLI/MCP call should write its evidence to.

    An explicit directory (non-empty) is used exactly as given. Otherwise a
    fresh per-run directory is reserved under `root`; if the call ends
    without writing anything into it (e.g. the input was rejected), the
    empty directory is removed again so failed calls leave no litter.
    """
    if explicit:
        yield Path(explicit)
        return
    path = new_run_dir(root, create=not dry_run)
    try:
        yield path
    finally:
        if not dry_run:
            # rmdir() only succeeds on an empty directory - a run that wrote
            # evidence keeps it.
            with suppress(OSError):
                path.rmdir()
