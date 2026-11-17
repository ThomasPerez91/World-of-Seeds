#!/usr/bin/env python3
"""Capture the main screens of two frontend builds and compare them pixel by pixel.

Usage: capture.py --base BASE_DIST --head HEAD_DIST --out REPORT_DIR

Each build is served by a local static server. The API is simulated by fixtures.py and the
torrent event socket is accepted but stays silent, so no backend is needed. The browser clock is
frozen and animations are disabled, so two identical builds give identical pixels. The exit code
is 1 when a screen differs, and REPORT_DIR holds base, head and highlighted diff images.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageChops
from playwright.sync_api import Page, Route, WebSocketRoute, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import NOW, answer  # noqa: E402

VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}
# Hide the text caret and stop every animation or transition, spinners included.
STABILISE_CSS = (
    "*, *::before, *::after { caret-color: transparent !important; "
    "animation: none !important; transition: none !important; }"
)
# A screen counts as changed above this many clearly different pixels; below, it is reported
# as rendering noise (antialiasing of a small icon). A real style change touches far more.
NOISE_PIXELS = 20
CHANNEL_DELTA = 8


@dataclass(frozen=True)
class Screen:
    name: str
    scenario: str
    steps: Callable[[Page], None]


def _open_legal(page: Page) -> None:
    page.get_by_role("button", name="Mentions légales").first.click()


def _open_nav(label: str) -> Callable[[Page], None]:
    def step(page: Page) -> None:
        page.get_by_role("navigation", name="Navigation principale").get_by_role(
            "button", name=label
        ).click()

    return step


SCREENS = [
    Screen("login", "login", lambda page: page.locator(".login-card").wait_for()),
    Screen("legal", "legal", _open_legal),
    Screen("dashboard", "dashboard", lambda page: page.locator(".app-shell").wait_for()),
    Screen("settings", "dashboard", _open_nav("Paramètres")),
    Screen("admin-users", "dashboard", _open_nav("Administration")),
]


class _SpaHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def send_head(self):  # type: ignore[no-untyped-def]
        path = Path(self.translate_path(self.path))
        if not path.exists():
            self.path = "/index.html"
        return super().send_head()


@contextmanager
def serve(directory: Path) -> Iterator[str]:
    handler = functools.partial(_SpaHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _api_handler(scenario: str, unknown: set[str]) -> Callable[[Route], None]:
    def handle(route: Route) -> None:
        request = route.request
        path = urlsplit(request.url).path
        result = answer(scenario, request.method, path)
        if result is None:
            unknown.add(f"{request.method} {path}")
            result = (
                404,
                {"detail": {"code": "not_simulated", "message": "Not simulated", "field": None}},
            )
        status, body = result
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    return handle


def _silent_socket(socket: WebSocketRoute) -> None:
    # Accept the connection without connecting to a server; no event is ever sent.
    return None


def capture(
    dist: Path, out: Path, unknown: set[str], unstable: set[str], viewer: object
) -> dict[str, Path]:
    shots: dict[str, Path] = {}
    out.mkdir(parents=True, exist_ok=True)
    browser = viewer  # a launched Playwright browser
    with serve(dist) as origin:
        for screen in SCREENS:
            for viewport, (width, height) in VIEWPORTS.items():
                context = browser.new_context(  # type: ignore[attr-defined]
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    locale="fr-FR",
                    timezone_id="Europe/Paris",
                    reduced_motion="reduce",
                    color_scheme="dark",
                )
                page = context.new_page()
                page.clock.set_fixed_time(NOW)
                page.route("**/api/**", _api_handler(screen.scenario, unknown))
                page.route_web_socket("**/api/v2/torrents/events", _silent_socket)
                page.goto(origin + "/", wait_until="networkidle")
                screen.steps(page)
                page.wait_for_load_state("networkidle")
                page.evaluate("document.fonts.ready")
                page.add_style_tag(content=STABILISE_CSS)
                path = out / f"{screen.name}-{viewport}.png"
                image, stable = _settled_screenshot(page)
                path.write_bytes(image)
                shots[path.name] = path
                if not stable:
                    unstable.add(path.name)
                context.close()
    return shots


def _neutral_screenshot(page: Page) -> bytes:
    # The page may move focus once its data loads: clear focus and hover right before capture.
    page.mouse.move(0, 0)
    page.evaluate("document.activeElement instanceof HTMLElement && document.activeElement.blur()")
    return page.screenshot(full_page=True, animations="disabled")


def _settled_screenshot(page: Page, attempts: int = 10) -> tuple[bytes, bool]:
    """Screenshot once two consecutive captures are identical (polling, late images, toasts)."""
    previous = _neutral_screenshot(page)
    for _ in range(attempts):
        page.wait_for_timeout(400)
        current = _neutral_screenshot(page)
        if current == previous:
            return current, True
        previous = current
    return previous, False


def compare(base: Path, head: Path, diff_path: Path) -> tuple[int, str]:
    """Differing pixels and their bounding box; writes a highlighted diff image if any."""
    with Image.open(base) as before, Image.open(head) as after:
        before_rgb, after_rgb = before.convert("RGB"), after.convert("RGB")
        if before_rgb.size != after_rgb.size:
            width = max(before_rgb.width, after_rgb.width)
            height = max(before_rgb.height, after_rgb.height)
            canvas_before = Image.new("RGB", (width, height), (255, 0, 255))
            canvas_after = Image.new("RGB", (width, height), (255, 0, 255))
            canvas_before.paste(before_rgb)
            canvas_after.paste(after_rgb)
            before_rgb, after_rgb = canvas_before, canvas_after
        red, green, blue = ImageChops.difference(before_rgb, after_rgb).split()
        strongest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        mask = strongest.point(lambda v: 255 if v > CHANNEL_DELTA else 0)
        changed = mask.histogram()[255]
        box = mask.getbbox()
        where = f"x {box[0]}–{box[2]}, y {box[1]}–{box[3]}" if box else ""
        if before.size != after.size:
            sizes = f"{before.size[0]}×{before.size[1]} → {after.size[0]}×{after.size[1]}"
            where = f"taille {sizes}; {where}"
        if changed:
            highlight = Image.blend(after_rgb, Image.new("RGB", after_rgb.size, (0, 0, 0)), 0.6)
            highlight.paste(Image.new("RGB", after_rgb.size, (255, 0, 64)), mask=mask)
            highlight.save(diff_path)
        return changed, where


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for dist in (args.base, args.head):
        if not (dist / "index.html").is_file():
            parser.error(f"{dist} is not a built frontend (index.html missing)")

    unknown: set[str] = set()
    unstable: set[str] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            base_shots = capture(args.base, args.out / "base", unknown, unstable, browser)
            head_shots = capture(args.head, args.out / "head", unknown, unstable, browser)
        finally:
            browser.close()

    rows: list[str] = []
    changed_screens = 0
    (args.out / "diff").mkdir(parents=True, exist_ok=True)
    for name in sorted(base_shots):
        changed, where = compare(base_shots[name], head_shots[name], args.out / "diff" / name)
        if changed > NOISE_PIXELS:
            changed_screens += 1
            result = f"**{changed} px modifiés** ({where})"
        elif changed:
            result = f"identique (bruit : {changed} px, {where})"
        else:
            result = "identique"
        if name in unstable:
            result += " — capture instable"
        rows.append(f"| `{name}` | {result} |")

    report = [
        "## Comparaison visuelle",
        "",
        f"{len(rows)} captures, {changed_screens} différentes.",
        "",
        "| Écran | Résultat |",
        "|---|---|",
        *rows,
    ]
    if unstable:
        report += [
            "",
            "Captures instables (l'écran bougeait encore après 10 essais) :",
            *[f"- `{name}`" for name in sorted(unstable)],
        ]
    if unknown:
        report += [
            "",
            "Appels API non simulés (réponse 404) :",
            *[f"- `{call}`" for call in sorted(unknown)],
        ]
    text = "\n".join(report) + "\n"
    (args.out / "report.md").write_text(text, encoding="utf-8")
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if changed_screens else 0


if __name__ == "__main__":
    raise SystemExit(main())
