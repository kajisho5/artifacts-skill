# Security model

Office-family and PDF files are executable-adjacent (macros, JavaScript
actions, zip-bomb-shaped archives, path-escaping archive members) even
though they "look like documents." This project treats them that way from
the start, not as an afterthought. "It runs locally" is not treated as a
substitute for these controls — a local process that gets tricked into a
zip bomb or a path escape is still a real incident.

## Subprocess execution — `security/subprocess_exec.py`

This module is the *only* place in the codebase allowed to call
`subprocess`. Rules, all enforced in code (not just documented):

- **argv array only.** `run(argv, ...)` rejects anything that isn't a list
  of strings; `shell=True` is never used anywhere in this project.
- **Explicit allowlist.** Every call site passes its own small `allowlist`
  set of acceptable executable *names* (e.g. `{"soffice"}` for a future
  LibreOffice-backed adapter). An executable not on that allowlist raises
  `ARTIFACT_SUBPROCESS_NOT_ALLOWLISTED` before anything runs.
- **Resolved, verified path.** The executable is resolved via `shutil.which`
  and checked to exist before exec; a missing binary raises
  `ARTIFACT_EXECUTABLE_NOT_FOUND` with a `doctor`-pointing remediation
  instead of a raw `FileNotFoundError`.
- **Timeout, always.** Every call has an effective timeout
  (`Limits.subprocess_timeout_seconds`, default 120s); a timeout is reported
  as a structured result (`timed_out=True`), not an uncaught exception.
- **Isolated working directory** by default (a fresh `TemporaryDirectory`
  unless the caller passes an explicit `cwd`).
- **Minimal environment.** Only `PATH`, `HOME`, `TMPDIR`/`TEMP`/`TMP`,
  `LANG`, `LC_ALL`, `SYSTEMROOT` are forwarded — not the caller's full
  environment (proxies, tokens, unrelated config).

**No orphaned children on interrupt (Issue #25).** A `soffice`/Chromium
child killed mid-render must not outlive the parent CLI/MCP process.
Verified directly against CPython's own `subprocess.py`, not assumed:
`subprocess.run()` already kills its child on *any* Python-level exception
escaping `communicate()` — including `KeyboardInterrupt`, which is exactly
what SIGINT (a real Ctrl-C) raises by default, so that case was never
actually broken. SIGTERM is different: its default disposition terminates
the process immediately with no Python exception raised at all, so nothing
downstream — no `finally`, no `except` clause anywhere in this codebase —
ever gets a chance to run, silently orphaning the child. `cli/main.py`'s
`main()` and `mcp/server.py`'s `serve()` — the two real process entry
points — both call
`security/subprocess_exec.py::treat_sigterm_as_interrupt()` on startup,
which installs `signal.default_int_handler` for SIGTERM too, so it now
raises the exact same `KeyboardInterrupt` SIGINT already did and gets the
same, already-correct cleanup. `tests/security/test_subprocess_exec.py`
proves both halves empirically (spawning a real child process and sending
it a real SIGTERM), not just by reading the code: the bug is demonstrated
first, then the fix is shown to prevent it.

A `receipt.json` is the only evidence that verification actually ran to
completion — an output *file* existing on disk after an interrupted
`execute`/`receipt` call is not proof of anything: `execute`'s own atomic
write (see "Filesystem" below) guarantees that file is never partially
written, but says nothing about whether verification ever happened
afterward. Treat a missing receipt exactly like a missing output: rerun.

The PDF and Image adapters never need this module — `pypdf`, `pypdfium2`,
and Pillow are pure-Python-callable. `rendering/office_convert.py` (used
by PPTX/DOCX/XLSX for LibreOffice-backed rendering) is the module's actual
consumer, going through `run()` with an explicit `{"soffice",
"libreoffice"}` allowlist for every invocation.

**Cross-platform allowlist matching.** Adapters write allowlists using
platform-neutral executable names (`"soffice"`), but the resolved,
absolute path `shutil.which()` returns on Windows carries an executable
extension (`soffice.exe`) that a bare allowlist name never would —
comparing the two directly would reject every resolved executable on
Windows. Found by direct code audit for Issue #10's cross-platform
verification, not by an actual Windows CI run (this project's CI only
runs `ubuntu-latest`). `run()`'s `_executable_basename()` strips a known
executable extension (`.exe`/`.bat`/`.cmd`/`.com`, case-insensitively)
before the allowlist check, and does so by explicitly parsing a
backslash-containing path with the stdlib's `ntpath` module regardless of
which OS is actually running the code — `pathlib.Path` alone only
understands `\` as a separator when Python itself is running on Windows,
which would make the fix untestable on this Linux-only CI. Branching on
`ntpath` when a backslash is present is what makes
`tests/security/test_subprocess_exec.py`'s Windows-path-shaped cases
genuinely pass here, rather than merely being asserted to work on a
platform nothing in this repository ever runs on.

**Documented exception: the HTML adapter's Playwright/Chromium process.**
`adapters/html/adapter.py::render()` calls Playwright's Python API
(`sync_playwright()`, `chromium.launch()`), which spawns and manages its
own browser process internally — that process is not started through this
module's `run()`. The rationale this module exists for — no shell strings,
no attacker-influenced argv assembled by this project's own code — applies
to argv *this project constructs*, like the `soffice` invocation in
`rendering/office_convert.py`. It does not extend to a well-audited
library (Playwright) managing its own child process through its own API,
any more than it would require wrapping `pypdfium2`'s internal calls into
PDFium. What this project *is* responsible for at that boundary — and
does enforce — is what the browser process is allowed to do once running:
`render()` installs a Playwright route handler that aborts every request
that isn't `file://`/`data:`/`about:`, so navigating to an HTML page can
never trigger a real network fetch, matching the network-off-by-default
policy below in an actively-enforced way, not just a documented one.

## Filesystem — `security/paths.py`

- **`resolve_within(base_dir, candidate)`** — resolves a path and asserts
  it stays inside `base_dir`; used for every archive-member path and every
  output path before it touches disk. Raises `ARTIFACT_PATH_ESCAPE` on
  `..`, an absolute escape, or a symlink that resolves outside the base.
- **`atomic_write_bytes` / `atomic_copy`** — write to a same-directory temp
  file, `fsync`, then `os.replace`. No partial file is ever left at the
  target path, including on a crash mid-write.
- **`safe_extract_zip`** — the entry point for any zip-based container
  (used today by the OOXML content-type sniff in `core/artifact.py`'s type
  detection, and reserved for the PPTX/DOCX/XLSX adapters' own extraction).
  Rejects, before extracting anything:
  - more members than `Limits.max_zip_members` (default 20,000),
  - any member with an absolute path or a `..` segment,
  - any symlink member (these can point extraction output outside the
    destination directory even when the *name* looks safe),
  - total uncompressed size beyond `Limits.max_zip_uncompressed_bytes`
    (default 2 GB),
  - any single member whose uncompressed size exceeds its compressed size
    by more than `Limits.max_zip_compression_ratio` (default 200x) when
    that member is itself larger than 10 MB — the classic zip-bomb
    signature, gated on absolute size so legitimately-compressible small
    files (e.g. an empty XML part) don't false-positive.
- **`check_input_size`** — rejects an input file above
  `Limits.max_input_bytes` (default 500 MB) before any adapter opens it.

## XML entity expansion — `security/xml_safety.py` (Issue #21)

XML is XML regardless of what format wraps it, and `xml.etree.ElementTree`
(like most `expat`-based parsers) is not hardened against entity-expansion
DoS ("billion laughs") by default: a few bytes of nested `<!ENTITY>`
definitions can expand to gigabytes in memory *during parsing*, before the
existing `check_input_size` file-size cap gets a chance to matter.

`security/xml_safety.py::reject_xml_entity_declaration()` reads the first
64 KB of a document and refuses to parse anything that declares a
`<!ENTITY` or a `<!DOCTYPE` with an internal subset (`[...]`), raising
`ARTIFACT_XML_ENTITY_DECLARATION_REJECTED` before the file ever reaches
`ElementTree`, lxml, or Chromium. Legitimate documents have no legitimate
use for a DOCTYPE/ENTITY declaration, so this is a hard rejection, not a
size-limited allowance. Two callers:

- **`reject_xml_entities_in_file()`** — a single on-disk XML file. Used by
  the SVG adapter (originally the only caller, before this module was
  generalized out of `adapters/svg/adapter.py`) at the start of both
  `inspect()` and `render()` — a rendered SVG goes through Chromium's own
  XML parser too, so the same file must be rejected before either path.
- **`reject_xml_entities_in_zip()`** — every `.xml` member of an OOXML zip
  (`.pptx`/`.docx`/`.xlsx`), scanned before the format-specific library
  opens the file at all.

**Why the zip variant exists — an actual audit, not a hypothetical.**
Tracker issue #2 had left open, since the first external review pass,
whether PPTX/DOCX/XLSX's internal XML parsing (python-pptx/python-docx/
openpyxl) shares this exposure. Checked directly, not assumed:

- **python-pptx and python-docx: not exposed.** Both explicitly construct
  their `lxml.etree.XMLParser` with `resolve_entities=False` at every XML
  entry point (`pptx/oxml/__init__.py`, `docx/oxml/parser.py`,
  `docx/opc/oxml.py`) — confirmed by reading their installed source *and*
  by round-tripping a crafted `.pptx`/`.docx` with a DOCTYPE-declared
  entity through `Presentation()`/`Document()`: the entity was dropped
  from the extracted text entirely, neither resolved nor left as literal
  text. `reject_xml_entities_in_zip()` is still wired into
  `XlsxAdapter.inspect()`/`execute()` alone, not PPTX/DOCX's — adding it
  there too would be a guard against a risk direct testing didn't find,
  which this project's own "don't validate for scenarios that can't
  happen" stance argues against.
- **openpyxl: exposed, confirmed by direct testing.** `openpyxl.xml.
  functions` only swaps in a hardened parser (`lxml` with
  `resolve_entities=False`, or `defusedxml`) when one of those packages
  happens to be importable — and this project's own `xlsx` extra
  (`pyproject.toml`) pulls in neither. A `pip install -e ".[xlsx]"`-only
  install — a real, documented, minimal install path — gets *zero*
  entity-expansion protection from openpyxl by default. Verified
  exploitable: a crafted `xl/worksheets/sheet1.xml` with a DOCTYPE-declared
  entity had its value substituted directly into a cell
  (`ws["A1"].value == "PWNED_VALUE"`), and a small nested-entity chain
  amplified exactly as the classic "billion laughs" pattern predicts. A
  `SYSTEM "file://..."` external entity was *not* resolved (expat's
  default posture doesn't fetch external entities), so this is a DoS/
  data-integrity risk, not full XXE file disclosure. Fixed:
  `XlsxAdapter.inspect()`/`execute()` both call
  `reject_xml_entities_in_zip()` before `openpyxl.load_workbook()` —
  `tests/fixtures/xlsx/entity_bomb.xlsx` and
  `tests/security/test_xml_safety.py` cover it, along with a direct
  adapter-level regression test.

The fix operates below the format-specific library entirely (a zip-level
pre-scan) rather than trying to reconfigure or monkeypatch openpyxl's
internal parser choice — that would be a fragile dependency on its exact
import structure, liable to silently stop protecting anything on a future
openpyxl version bump.

## Resource limits — `security/limits.py`

A single `Limits` dataclass (`DEFAULT_LIMITS`) is the only place any of
these numbers live. No adapter hardcodes its own "too big" constant. A
caller who genuinely needs to raise a limit does so by passing a different
`Limits` instance explicitly (visible in code review), never by a hidden
per-format exception.

## Network policy

Off by default, everywhere, with no per-tool opt-out in the current MVP.
Nothing in this codebase issues an HTTP request itself. The HTML adapter
is the one place a *sub-process this project doesn't control* (Chromium)
could otherwise do so on its own — `render()` actively blocks it via a
Playwright route handler that aborts every non-`file://`/`data:`/`about:`
request rather than merely documenting an intention (verified in
`tests/unit/test_html_adapter.py::test_render_blocks_external_requests_
when_backend_works`, which drives a real browser and confirms an external
request is attempted-then-aborted, not fulfilled). `verify_structural()`'s
`external_resources` check reports the same references as `WARN` (or
`FAIL` under `policy.forbid_external_resources`) so the caller knows why a
render might show broken images/missing styles, rather than being
surprised by it. If a future capability needs to actually fetch remote
assets to render faithfully, that will be an explicit, separately-gated
capability (`html.fetch_remote_assets` or similar) that defaults to *off*
— consistent with spec §27.

## Original Protection

`core/operation.py:default_output_path()` never returns the input path.
Every `execute()` implementation writes only to the `output_path` it is
given; the PDF adapter's `execute()` never opens `ref.path` in write mode.
`tests/security/test_original_protection.py` asserts the input file's
SHA-256 is bit-identical before and after every operation, including when a
caller passes `--output` equal to the input path (Original Protection wins
over the caller's literal request — see that test for the exact behavior).

## What's explicitly not handled yet (and why that's stated, not hidden)

- **Macro execution**: out of scope entirely — this project never executes
  embedded macros/scripts in any artifact, and has no code path that could.
- **Malware/antivirus scanning**: out of scope. This project's guarantees
  are about structural correctness and safe extraction, not content
  safety classification.
