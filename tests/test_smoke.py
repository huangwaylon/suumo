"""Smoke test of the site in a headless browser: a site built from tests/fixtures/data (plus page side files from the
fixture listing pages), served on localhost, driven through the page like a user (desktop and phone).

Offline and deterministic: every request off localhost is answered here (Leaflet from the vendored copy in
tests/fixtures/leaflet, map tiles and SUUMO photos as a 1x1 PNG, anything else empty), the service worker is blocked.
It drives the DOM (ids, roles, visible text), not app.js internals, so it outlives a restructuring of the scripts.

Skipped unless Playwright is installed; run it with
  uv run --with playwright==1.65.2 playwright install --only-shell chromium   (once)
  uv run --with playwright==1.65.2 pytest tests/test_smoke.py -q
"""
import base64
import functools
import gzip
import json
import re
import shutil
import threading
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from suumo.export import page_line
from suumo.extract import extract
from suumo.site import build
from tests.helpers import FIXTURES, QUIET

sync_api = pytest.importorskip("playwright.sync_api", reason="playwright not installed (see the module docstring)")
expect = sync_api.expect
expect.set_options(timeout=10_000)

ROOT = Path(__file__).parent.parent
LEAFLET = FIXTURES / "leaflet"  # leaflet@1.9.4 dist (BSD-2-Clause), byte for byte: app.js loads it with SRI
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
DEEP = "used_condo:20205670"  # a fixture listing with its page (photos) and a known price
DEEP_PRICE = "1,200万円"
VIEWS = {"desktop": {"width": 1280, "height": 800}, "phone": {"width": 390, "height": 844}}


def build_site(tmp):
    """tests/fixtures/data + page side files extracted from the fixture listing pages + Komae's town points."""
    data = tmp / "data"
    shutil.copytree(FIXTURES / "data", data)
    for path in sorted(FIXTURES.glob("detail_*.html.gz")):
        type_, id_ = re.fullmatch(r"detail_(.+)_(\d+)\.html\.gz", path.name).groups()
        page = extract(gzip.decompress(path.read_bytes()).decode("utf-8"), type_, id_)
        line = page_line({"id": id_, "page_json": json.dumps(page, ensure_ascii=False)})
        out = data / "tokyo" / "pages" / type_ / "13219.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    towns = json.loads((ROOT / "geo" / "towns.json").read_text(encoding="utf-8"))
    geo = tmp / "towns.json"
    geo.write_text(json.dumps({k: v for k, v in towns.items() if k.startswith("東京都狛江市")}, ensure_ascii=False))
    (data / "fx.json").write_text('{"date": "2026-10-13", "jpy_per_usd": 158.12}')
    site = tmp / "site"
    build(data, geo, site, today=date(2026, 10, 6), log=QUIET, stations_cache=ROOT / "geo" / "stations.json",
          rail_path=ROOT / "geo" / "rail.json")
    return site


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # (the default 5 resets connections when the page opens many at once)
    daemon_threads = True


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    site = build_site(tmp_path_factory.mktemp("smoke"))
    httpd = Server(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def offline(route):
    """Every request off localhost: Leaflet from the vendored copy, images a 1x1 PNG, the rest empty."""
    url, kind = route.request.url, route.request.resource_type
    if m := re.match(r"https://unpkg\.com/leaflet@1\.9\.4/dist/(leaflet\.(?:js|css))$", url):
        types = {"js": "application/javascript", "css": "text/css"}
        route.fulfill(body=(LEAFLET / m[1]).read_bytes(), content_type=types[m[1].rsplit(".", 1)[1]],
                      headers={"Access-Control-Allow-Origin": "*"})
    elif kind == "image" or re.search(r"\.(png|jpe?g|gif|webp)(\?|$)|resizeImage", url):
        route.fulfill(body=PNG, content_type="image/png", headers={"Access-Control-Allow-Origin": "*"})
    else:
        route.fulfill(body=b"", headers={"Access-Control-Allow-Origin": "*"})


@pytest.fixture(params=list(VIEWS))
def page(request, browser, server):
    phone = request.param == "phone"
    ctx = browser.new_context(viewport=VIEWS[request.param], service_workers="block", locale="ja-JP",
                              is_mobile=phone, has_touch=phone, base_url=server)
    ctx.route(re.compile(r"^(?!http://127\.0\.0\.1:)"), offline)
    pg = ctx.new_page()
    pg.set_default_timeout(10_000)
    errors = []
    pg.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text} ({m.location})"))
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    yield pg
    ctx.close()
    assert not errors, "\n".join(errors)


def count(pg):
    """The number in the results heading (#count), e.g. "46件" or "12 of 46"."""
    text = pg.locator("#count").inner_text()
    return int(re.search(r"\d[\d,]*", text)[0].replace(",", ""))


def loaded(pg, url="./"):
    pg.goto(url)
    expect(pg.locator("#list .card").first).to_be_visible()
    return count(pg)


def changes(pg, before):
    expect(pg.locator("#count")).not_to_have_text(re.compile(rf"(^|\D){before:,}(\D|$)|(^|\D){before}(\D|$)"))
    return count(pg)


def open_filters(pg):
    """The filter panel: a rail from 1280 px wide (open by default), a sheet below."""
    if pg.viewport_size["width"] < 1280:
        pg.locator("#filters-open").click()
    expect(pg.locator("#filters [data-many='types']").first).to_be_visible()


def test_loads_with_cards(page):
    n = loaded(page)
    assert n > 0 and page.locator("#list .card").count() > 0
    expect(page.locator(".leaflet-container")).to_have_count(1)  # Leaflet (vendored, past the SRI check): the map


def test_suggestion_narrows_the_results(page):
    n = loaded(page)
    page.locator("#q").fill("喜多見")
    option = page.locator("#suggest [role=option], #suggest .sg").first
    expect(option).to_be_visible()
    option.click()
    assert 0 < changes(page, n) < n


def test_type_chip_changes_the_count(page):
    n = loaded(page)
    open_filters(page)
    chip = page.locator("#filters [data-many='types'][data-value='used_condo']")
    chip.click()
    expect(chip).to_have_attribute("aria-pressed", "true")
    if page.locator("#apply").is_visible():
        page.locator("#apply").click()
    assert 0 < changes(page, n) < n


def test_listing_photos_and_back(page):
    loaded(page)
    page.locator(f"#list .card a[data-key='{DEEP}']").click()
    body = page.locator("#panel-body")
    expect(body.locator("h2").first).to_be_visible()
    expect(body).to_contain_text(DEEP_PRICE)
    assert f"id={DEEP}" in page.url.replace("%3A", ":")
    body.locator("[data-view]").first.click()
    viewer = page.get_by_role("dialog", name=re.compile("写真|Photos"))
    expect(viewer).to_be_visible()
    page.keyboard.press("Escape")
    expect(viewer).to_have_count(0)
    expect(body).to_be_visible()
    page.go_back()
    expect(page.locator("#list .card").first).to_be_visible()
    expect(body).to_be_hidden()


def test_deep_link_opens_the_listing(page):
    page.goto(f"./#id={DEEP}")
    body = page.locator("#panel-body")
    expect(body).to_contain_text(DEEP_PRICE)
    expect(body.locator("[data-gallery]")).to_be_visible()  # the page side file's photos


def test_english(page):
    loaded(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings")).to_be_visible()
    page.locator("#settings [data-setting='lang'][data-value='en']").click()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    page.locator("#settings-close").click()
    expect(page.locator("#settings")).to_be_hidden()
    expect(page.locator("#filters-open")).to_have_attribute("aria-label", re.compile("Filters"))
    expect(page.locator("#list .card").first).to_contain_text("¥")


def test_favorites_view(page):
    loaded(page)
    page.locator("#list .card .fav").first.click()
    expect(page.locator("#favs-n")).to_have_text("1")
    page.locator("#favs").click()
    expect(page.locator("#favs")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#list .card")).to_have_count(1)
