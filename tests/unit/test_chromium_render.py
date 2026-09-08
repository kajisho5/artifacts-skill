"""Unit tests for rendering/chromium_render.py.

Adapter-level render() tests (test_html_adapter.py, test_svg_adapter.py)
cover the real end-to-end Playwright/Chromium path when a working browser
is available. This file isolates one specific thing those can't easily
prove deterministically without a real browser: that
`limits.render_timeout_seconds` (Issue #28) actually reaches Playwright's
`page.goto()`/`page.screenshot()` calls as the millisecond timeout, rather
than the old hardcoded, disconnected module constant — done by faking out
`sync_playwright()` entirely and recording what timeout value each call
received.
"""

from __future__ import annotations

import pytest

from artifact_skill.rendering import chromium_render
from artifact_skill.security.limits import Limits


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = type("Req", (), {"url": url})()

    def continue_(self) -> None:
        pass

    def abort(self) -> None:
        pass


class _FakePage:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls

    def route(self, pattern, handler) -> None:
        handler(_FakeRoute("file:///fake.html"))  # exercise the route handler once

    def goto(self, url, *, wait_until, timeout) -> None:
        self._calls.append(("goto", {"timeout": timeout}))

    def screenshot(self, *, path, full_page, timeout) -> None:
        self._calls.append(("screenshot", {"timeout": timeout}))
        with open(path, "wb") as f:
            f.write(b"fake png bytes")


class _FakeBrowser:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls

    def new_page(self, viewport):
        return _FakePage(self._calls)

    def close(self) -> None:
        pass


class _FakeChromium:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls

    def launch(self):
        return _FakeBrowser(self._calls)


class _FakePlaywrightContext:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.chromium = _FakeChromium(calls)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_fake_playwright(monkeypatch, calls: list[tuple[str, dict]]) -> None:
    playwright_sync_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setattr(playwright_sync_api, "sync_playwright", lambda: _FakePlaywrightContext(calls))


def test_default_limits_produce_the_documented_30s_timeout_in_ms(tmp_path, monkeypatch):
    calls: list[tuple[str, dict]] = []
    _install_fake_playwright(monkeypatch, calls)

    chromium_render.render_local_file(tmp_path / "in.html", tmp_path / "out.png", capability_id="html.render")

    assert ("goto", {"timeout": 30_000}) in calls
    assert ("screenshot", {"timeout": 30_000}) in calls


def test_a_custom_limits_render_timeout_reaches_goto_and_screenshot(tmp_path, monkeypatch):
    """The whole point of Issue #28's fix: a caller overriding
    Limits.render_timeout_seconds must actually change what Chromium does,
    not just change a dataclass field nothing reads."""
    calls: list[tuple[str, dict]] = []
    _install_fake_playwright(monkeypatch, calls)

    custom_limits = Limits(render_timeout_seconds=5)
    chromium_render.render_local_file(
        tmp_path / "in.html", tmp_path / "out.png", capability_id="html.render", limits=custom_limits
    )

    assert ("goto", {"timeout": 5_000}) in calls
    assert ("screenshot", {"timeout": 5_000}) in calls


# --- the navigation URL itself is a real, percent-encoded file:// URI -----
# (self-audit finding, FIX_PROMPT P1-3: a naive f"file://{path}" string
# doesn't percent-encode "#"/"?"/spaces - confirmed directly against a
# real Chromium launch that a "#" in a filename made navigation fail with
# net::ERR_FILE_NOT_FOUND, since "#..." was read as a URL fragment and
# silently dropped from the path. Path.as_uri() is also the only correct
# way to build a Windows file:// URI ("file:///C:/...").)


def test_goto_receives_a_properly_encoded_file_uri_not_a_naive_concatenation(tmp_path, monkeypatch):
    calls: list[tuple[str, dict]] = []
    urls: list[str] = []

    class _RecordingPage:
        def route(self, pattern, handler) -> None:
            pass

        def goto(self, url, *, wait_until, timeout) -> None:
            urls.append(url)
            calls.append(("goto", {"timeout": timeout}))

        def screenshot(self, *, path, full_page, timeout) -> None:
            calls.append(("screenshot", {"timeout": timeout}))
            with open(path, "wb") as f:
                f.write(b"fake png bytes")

    class _RecordingBrowser:
        def new_page(self, viewport):
            return _RecordingPage()

        def close(self) -> None:
            pass

    class _RecordingChromium:
        def launch(self):
            return _RecordingBrowser()

    class _RecordingContext:
        def __init__(self) -> None:
            self.chromium = _RecordingChromium()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    playwright_sync_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setattr(playwright_sync_api, "sync_playwright", lambda: _RecordingContext())

    source = tmp_path / "report#1 (final).html"
    source.write_text("<html></html>")
    chromium_render.render_local_file(source, tmp_path / "out.png", capability_id="html.render")

    assert urls == [source.resolve().as_uri()]
    assert "%23" in urls[0]  # '#' is percent-encoded, not a literal URL-fragment separator
    assert "#" not in urls[0].split("://", 1)[1]  # no literal '#' anywhere in the path portion
    assert " " not in urls[0]  # spaces must be percent-encoded, never literal


# --- file:// requests outside the document's own directory are blocked ----
# (Grok review P0-2: previously *any* file:// URL was allowed through
# unconditionally, letting a hostile document pull in an arbitrary local
# file - e.g. <iframe src="file:///etc/passwd"> - whose content would end
# up in the rendered PNG evidence. Reproduced against a real Chromium
# launch before this fix; see test_html_adapter.py/test_svg_adapter.py
# for the real-browser end-to-end proof. This file tests the pure
# boundary-check function directly, deterministically, with no browser.)


def test_file_url_within_the_allowed_directory_is_allowed(tmp_path):
    from artifact_skill.rendering.chromium_render import _file_url_is_within

    allowed_root = tmp_path
    (tmp_path / "local.png").write_bytes(b"x")
    assert _file_url_is_within(f"file://{tmp_path / 'local.png'}", allowed_root) is True


def test_file_url_in_a_subdirectory_of_the_allowed_directory_is_allowed(tmp_path):
    from artifact_skill.rendering.chromium_render import _file_url_is_within

    (tmp_path / "assets").mkdir()
    assert _file_url_is_within(f"file://{tmp_path / 'assets' / 'x.png'}", tmp_path) is True


def test_file_url_outside_the_allowed_directory_is_rejected(tmp_path):
    from artifact_skill.rendering.chromium_render import _file_url_is_within

    outside = tmp_path.parent / "secret.txt"
    assert _file_url_is_within(f"file://{outside}", tmp_path) is False


def test_file_url_for_etc_passwd_is_rejected(tmp_path):
    from artifact_skill.rendering.chromium_render import _file_url_is_within

    assert _file_url_is_within("file:///etc/passwd", tmp_path) is False


def test_file_url_with_percent_encoded_traversal_is_still_rejected(tmp_path):
    """file:///allowed/../../../etc/passwd, percent-encoded - the check
    must decode and resolve() before comparing, not compare raw strings."""
    from artifact_skill.rendering.chromium_render import _file_url_is_within

    traversal = f"file://{tmp_path}/%2e%2e/%2e%2e/etc/passwd"
    assert _file_url_is_within(traversal, tmp_path) is False


def test_non_file_url_is_never_allowed_by_this_check(tmp_path):
    from artifact_skill.rendering.chromium_render import _file_url_is_within

    assert _file_url_is_within("https://example.invalid/x.png", tmp_path) is False
    assert _file_url_is_within("data:image/png;base64,abc", tmp_path) is False


def test_render_local_file_blocks_a_file_url_escaping_the_document_directory(tmp_path, monkeypatch):
    """End-to-end through render_local_file() itself (fake Playwright, no
    real browser needed) - proves the fix is actually wired into the real
    route handler, not just that the helper function works in isolation."""
    calls: list[tuple[str, dict]] = []
    routed: list[tuple[str, str]] = []  # (url, "continue"|"abort")

    class _RecordingRoute:
        def __init__(self, url: str) -> None:
            self.request = type("Req", (), {"url": url})()

        def continue_(self) -> None:
            routed.append((self.request.url, "continue"))

        def abort(self) -> None:
            routed.append((self.request.url, "abort"))

    class _RecordingPage:
        def route(self, pattern, handler) -> None:
            doc_dir = tmp_path / "docroot"
            handler(_RecordingRoute(f"file://{doc_dir / 'page.html'}"))  # the document itself
            handler(_RecordingRoute(f"file://{doc_dir / 'sibling.png'}"))  # same dir - allowed
            handler(_RecordingRoute(f"file://{tmp_path / 'outside.txt'}"))  # escapes - blocked
            handler(_RecordingRoute("file:///etc/passwd"))  # blocked
            handler(_RecordingRoute("data:image/png;base64,abc"))  # allowed

        def goto(self, url, *, wait_until, timeout) -> None:
            calls.append(("goto", {"timeout": timeout}))

        def screenshot(self, *, path, full_page, timeout) -> None:
            with open(path, "wb") as f:
                f.write(b"fake png bytes")

    class _RecordingBrowser:
        def new_page(self, viewport):
            return _RecordingPage()

        def close(self) -> None:
            pass

    class _RecordingChromium:
        def launch(self):
            return _RecordingBrowser()

    class _RecordingPlaywrightContext:
        def __init__(self) -> None:
            self.chromium = _RecordingChromium()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    playwright_sync_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setattr(playwright_sync_api, "sync_playwright", lambda: _RecordingPlaywrightContext())

    doc_dir = tmp_path / "docroot"
    doc_dir.mkdir()
    source = doc_dir / "page.html"
    source.write_text("<html></html>")

    chromium_render.render_local_file(source, tmp_path / "out.png", capability_id="html.render")

    outcome = dict(routed)
    assert outcome[f"file://{doc_dir / 'page.html'}"] == "continue"
    assert outcome[f"file://{doc_dir / 'sibling.png'}"] == "continue"
    assert outcome[f"file://{tmp_path / 'outside.txt'}"] == "abort"
    assert outcome["file:///etc/passwd"] == "abort"
    assert outcome["data:image/png;base64,abc"] == "continue"
