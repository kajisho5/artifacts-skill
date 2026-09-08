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

No adapter in the current MVP (PDF) actually needs this module yet — `pypdf`
and `pypdfium2` are pure-Python-callable. It exists now because the very
next adapter (LibreOffice-backed PPTX/DOCX/XLSX rendering) will need it
immediately, and retrofitting safety rules onto an existing call site is
how they get skipped under time pressure.

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

## Resource limits — `security/limits.py`

A single `Limits` dataclass (`DEFAULT_LIMITS`) is the only place any of
these numbers live. No adapter hardcodes its own "too big" constant. A
caller who genuinely needs to raise a limit does so by passing a different
`Limits` instance explicitly (visible in code review), never by a hidden
per-format exception.

## Network policy

Off by default, everywhere, with no per-tool opt-out in the current MVP.
Nothing in this codebase issues an HTTP request. When a future HTML/SVG
adapter needs to fetch a document's own linked assets (remote fonts, remote
images, remote stylesheets) to render faithfully, that will be an explicit,
separately-gated capability (`html.fetch_remote_assets` or similar) that
defaults to *off* and reports referenced-but-unfetched externals as a
`WARN`, not a silent fetch — consistent with spec §27.

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
