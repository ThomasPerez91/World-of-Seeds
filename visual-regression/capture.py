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
import difflib
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
# Hide the text caret and stop every animation or transition, spinners included. Backdrop
# blurs are switched off too: software rendering blurs them tile by tile, slightly differently
# from one browser session to the next. A change limited to a backdrop blur goes unnoticed.
# Nothing can be hovered either: a full-page screenshot resizes the viewport, which can move a
# different element under the pointer while the capture is taken. Applied after the clicks.
STABILISE_CSS = (
    "*, *::before, *::after { caret-color: transparent !important; "
    "animation: none !important; transition: none !important; "
    "backdrop-filter: none !important; -webkit-backdrop-filter: none !important; "
    "pointer-events: none !important; }"
)
# A screen counts as changed above this many differing pixels; below, it is reported as
# rendering noise (antialiasing of a small icon). Any channel difference counts, so even a
# faint background shade change over a whole screen is caught.
NOISE_PIXELS = 20


# The version number shows in the header and footer: every release would otherwise change every
# authenticated screen. It is replaced by a fixed label of the same shape before capture.
FIXED_VERSION = """() => {
for (const element of document.querySelectorAll('.version-badge, .app-footer > span')) {
  for (const node of element.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) {
      node.textContent = node.textContent.replace(/\\d+\\.\\d+\\.\\d+/, '0.0.0');
    }
  }
}
}"""


# For each visible element that paints text or graphics, its page box and the computed styles
# most likely to change its pixels. Used to name what changed inside a modified area.
DESCRIBE_ELEMENTS = """() => {
  const properties = ['color', 'background-color', 'opacity', 'font-family', 'font-size',
    'font-weight', 'letter-spacing', 'line-height', 'text-decoration-line', 'text-shadow',
    'box-shadow', 'border-color', 'transform', 'filter', 'visibility', 'fill', 'stroke'];
  const out = [];
  for (const element of document.body.querySelectorAll('*')) {
    const rect = element.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    const ownText = [...element.childNodes].some(
      (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim() !== '');
    if (!ownText && !(element instanceof SVGElement) && !(element instanceof HTMLImageElement)) {
      continue;
    }
    const style = getComputedStyle(element);
    const classes = [...element.classList].map((c) => '.' + c).join('');
    const name = element.tagName.toLowerCase() + classes;
    out.push({
      name,
      text: ownText ? element.textContent.trim().slice(0, 60) : '',
      box: [rect.left + scrollX, rect.top + scrollY, rect.right + scrollX, rect.bottom + scrollY],
      style: Object.fromEntries(properties.map((p) => [p, style.getPropertyValue(p)])),
    });
  }
  return out;
}"""


@dataclass(frozen=True)
class Screen:
    name: str
    scenario: str
    steps: Callable[[Page], None]
    # Clear focus before capture. Off for menus that close when they lose focus.
    blur: bool = True


def _open_legal(page: Page) -> None:
    page.get_by_role("button", name="Mentions légales").first.click()


def _open_nav(label: str) -> Callable[[Page], None]:
    def step(page: Page) -> None:
        page.get_by_role("navigation", name="Navigation principale").get_by_role(
            "button", name=label
        ).click()

    return step


def _then(*steps: Callable[[Page], None]) -> Callable[[Page], None]:
    def step(page: Page) -> None:
        for each in steps:
            each(page)
            page.wait_for_load_state("networkidle")

    return step


def _open_section(navigation: str, label: str) -> Callable[[Page], None]:
    def step(page: Page) -> None:
        page.get_by_role("navigation", name=navigation).get_by_role(
            "button", name=label, exact=True
        ).click()

    return step


def _settings(label: str) -> Callable[[Page], None]:
    return _then(_open_nav("Paramètres"), _open_section("Sections des paramètres", label))


def _admin(label: str) -> Callable[[Page], None]:
    return _then(_open_nav("Administration"), _open_section("Sections d’administration", label))


def _open_details(page: Page) -> None:
    button = page.get_by_role(
        "button", name="Afficher les détails de Cours.Video.Programmation.Python"
    )
    panel = button.get_attribute("aria-controls")
    button.click()
    page.locator(f"#{panel}").wait_for()


def _open_account_menu(page: Page) -> None:
    page.get_by_role("button", name="Ouvrir le menu du compte").click()
    page.locator("#account-dropdown").wait_for()


SCREENS = [
    Screen("login", "login", lambda page: page.locator(".login-card").wait_for()),
    Screen("legal", "legal", _open_legal),
    Screen("dashboard", "dashboard", lambda page: page.locator(".app-shell").wait_for()),
    Screen("dashboard-details", "dashboard", _open_details),
    Screen("account-menu", "dashboard", _open_account_menu, blur=False),
    Screen("settings", "dashboard", _open_nav("Paramètres")),
    Screen("settings-security", "dashboard", _settings("Sécurité")),
    Screen("settings-secrets", "dashboard", _settings("Secrets")),
    Screen("admin-users", "dashboard", _open_nav("Administration")),
    Screen("admin-qbittorrent", "dashboard", _admin("qBittorrent")),
    Screen("admin-cleanup", "dashboard", _admin("Nettoyage")),
    Screen("admin-settings", "dashboard", _admin("Paramètres")),
    Screen("admin-storage", "dashboard", _admin("Stockage")),
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
                page.evaluate(FIXED_VERSION)
                path = out / f"{screen.name}-{viewport}.png"
                image, stable = _settled_screenshot(page, blur=screen.blur)
                path.write_bytes(image)
                text = page.locator("body").inner_text()
                path.with_suffix(".txt").write_text(text, encoding="utf-8")
                elements = page.evaluate(DESCRIBE_ELEMENTS)
                path.with_suffix(".json").write_text(json.dumps(elements), encoding="utf-8")
                shots[path.name] = path
                if not stable:
                    unstable.add(path.name)
                context.close()
    return shots


def _neutral_screenshot(page: Page, blur: bool) -> bytes:
    # The page may move focus once its data loads: clear focus and hover right before capture.
    page.mouse.move(0, 0)
    if blur:
        page.evaluate(
            "document.activeElement instanceof HTMLElement && document.activeElement.blur()"
        )
    return page.screenshot(full_page=True, animations="disabled")


def _settled_screenshot(page: Page, blur: bool, attempts: int = 12) -> tuple[bytes, bool]:
    """Screenshot once three consecutive captures, 400 ms apart, are identical.

    Polling, late images and late layout can change a screen after a first quiet moment.
    """
    previous = _neutral_screenshot(page, blur)
    identical = 0
    for _ in range(attempts):
        page.wait_for_timeout(400)
        current = _neutral_screenshot(page, blur)
        identical = identical + 1 if current == previous else 0
        if identical == 2:
            return current, True
        previous = current
    return previous, False


def _changed_text(base: Path, head: Path, limit: int = 6) -> list[str]:
    """Visible text lines that differ between the two captures of a screen."""
    if not base.is_file() or not head.is_file():
        return []
    before = base.read_text(encoding="utf-8").splitlines()
    after = head.read_text(encoding="utf-8").splitlines()
    lines = [
        line
        for line in difflib.unified_diff(before, after, lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    return lines[:limit]


def _elements_in(path: Path, box: tuple[int, int, int, int]) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    left, top, right, bottom = box
    return [
        element
        for element in json.loads(path.read_text(encoding="utf-8"))
        if element["box"][0] < right
        and element["box"][2] > left
        and element["box"][1] < bottom
        and element["box"][3] > top
    ]


def _describe_area(
    base: Path, head: Path, box: tuple[int, int, int, int], limit: int = 8
) -> list[str]:
    """Elements overlapping a changed area, with the computed styles that differ."""
    before = _elements_in(base, box)
    after = _elements_in(head, box)
    lines: list[str] = []
    for index, element in enumerate(after[:limit]):
        label = f"`{element['name']}`" + (f" « {element['text']} »" if element["text"] else "")
        match = next(
            (
                other
                for other in before
                if other["name"] == element["name"] and other["text"] == element["text"]
            ),
            None,
        )
        if match is None:
            lines.append(f"- {label} : absent avant")
            continue
        changes = [
            f"{prop} `{match['style'][prop]}` → `{value}`"
            for prop, value in element["style"].items()
            if match["style"].get(prop) != value
        ]
        if match["box"] != element["box"]:
            changes.append(f"boîte {match['box']} → {element['box']}")
        lines.append(f"- {label} : " + ("; ".join(changes) if changes else "styles identiques"))
    if len(after) > limit:
        lines.append(f"- … et {len(after) - limit} autres éléments")
    if not after and before:
        lines.append(f"- {len(before)} élément(s) présents avant seulement")
    return lines


def compare(
    base: Path, head: Path, diff_path: Path
) -> tuple[int, str, tuple[int, int, int, int] | None]:
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
        mask = strongest.point(lambda v: 255 if v else 0)
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
        return changed, where, box


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
    changed_texts: list[tuple[str, list[str], list[str]]] = []
    changed_screens = 0
    (args.out / "diff").mkdir(parents=True, exist_ok=True)
    for name in sorted(base_shots):
        changed, where, box = compare(
            base_shots[name], head_shots[name], args.out / "diff" / name
        )
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
        if changed > NOISE_PIXELS:
            texts = _changed_text(
                base_shots[name].with_suffix(".txt"), head_shots[name].with_suffix(".txt")
            )
            area = (
                _describe_area(
                    base_shots[name].with_suffix(".json"),
                    head_shots[name].with_suffix(".json"),
                    box,
                )
                if box
                else []
            )
            if texts or area:
                changed_texts.append((name, texts, area))

    report = [
        "## Comparaison visuelle",
        "",
        f"{len(rows)} captures, {changed_screens} différentes.",
        "",
        "| Écran | Résultat |",
        "|---|---|",
        *rows,
    ]
    for name, texts, area in changed_texts:
        report += ["", f"### `{name}`"]
        if texts:
            report += ["", "Texte modifié :", "", "```diff", *texts, "```"]
        if area:
            report += ["", "Éléments dans la zone modifiée :", "", *area]
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
