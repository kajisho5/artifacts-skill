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
