#!/usr/bin/env python3
"""Secret-safe Web API acceptance probe, run inside the disposable worker network."""

import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ORIGIN = "http://qbittorrent:8080"


def main() -> None:
    route = json.loads(Path("/run/secrets/integration_registry").read_text())["routes"][0]
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )

    def request(path: str, data: dict[str, str] | None = None, **headers: str) -> tuple[int, bytes]:
        req = urllib.request.Request(
            ORIGIN + "/api/v2/" + path,
            data=urllib.parse.urlencode(data).encode() if data is not None else None,
            headers=headers,
        )
        try:
            with opener.open(req, timeout=10) as response:
                return response.status, response.read(256 * 1024)
        except urllib.error.HTTPError as error:
            return error.code, error.read(1024)

    def require(condition: bool, label: str) -> None:
        if not condition:
            raise RuntimeError(label)

    status, _ = request("torrents/info")
    require(status in {401, 403}, "unauthenticated inventory must be refused")
    status, body = request(
        "auth/login",
        {
            "username": route["qbittorrent_username"],
            "password": "intentionally-wrong-disposable-password",
        },
    )
    require(
        status in {401, 403} or (status == 200 and body.strip() == b"Fails."),
        "incorrect password must be refused",
    )
    status, body = request(
        "auth/login",
        {"username": route["qbittorrent_username"], "password": route["qbittorrent_password"]},
    )
    require(
        status == 204 or (status == 200 and body.strip() == b"Ok."), "WOS-like login must succeed"
    )
    status, body = request("app/version")
    require(status == 200 and body.strip() == b"v5.2.3", "qB runtime must remain 5.2.3")
    status, body = request("app/preferences")
    require(status == 200, "preferences must be readable after authentication")
    prefs = json.loads(body)
    expected = {
        "web_ui_host_header_validation_enabled": True,
        "web_ui_csrf_protection_enabled": True,
        "bypass_local_auth": False,
        "bypass_auth_subnet_whitelist_enabled": False,
        "proxy_type": "HTTP",
        "proxy_ip": "newgreedy",
        "proxy_port": 3456,
        "proxy_auth_enabled": False,
        "proxy_bittorrent": True,
        "proxy_peer_connections": False,
        "proxy_rss": False,
        "proxy_misc": False,
    }
    for key, value in expected.items():
        require(prefs.get(key) == value, "qB preference mismatch: " + key)
    domains = prefs["web_ui_domain_list"].split(";")
    require(
        "qbittorrent" in domains and not any("*" in d for d in domains),
        "explicit qB hostname without wildcard required",
    )
    status, body = request("torrents/info")
    require(status == 200 and json.loads(body) == [], "fresh inventory must contain zero items")
    status, _ = request("app/preferences", Host="untrusted.example.invalid:8080")
    require(status in {400, 401, 403}, "invalid Host must be refused")
    status, _ = request(
        "app/setPreferences", {"json": "{}"}, Origin="http://untrusted.example.invalid"
    )
    require(status in {400, 401, 403}, "cross-origin mutation must be refused")
    if "--sentinel" in sys.argv:
        require(prefs.get("max_connec") == 137, "unrelated existing preference must survive")
    if "--set-sentinel" in sys.argv:
        status, _ = request("app/setPreferences", {"json": json.dumps({"max_connec": 137})})
        require(status == 200, "test preference must be saved")
    print(
        "PASS: internal auth; wrong password/Host/Origin rejected; "
        "Host+CSRF; tracker proxy; peers direct; inventory=0"
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        sys.exit("qB probe failed: " + str(error))
    except Exception:
        sys.exit("qB probe failed: unavailable service, secret or malformed response")
