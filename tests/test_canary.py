"""The canary (suumo/canary.py) against the fixture pages, through a fake client: no network."""
import gzip
import re

from suumo import canary, detail
from suumo.extract import extract
from suumo.http import NetworkError, Unavailable
from suumo.parse import TYPES, parse_areas
from tests.helpers import FIXTURES, QUIET, rec, write

LISTS = {"used_condo": "used_condo", "new_condo": "new_condo", "new_house": "new_house", "used_house": "new_house",
         "land": "new_house"}
PAGES = {"20205670": "detail_used_condo_20205670", "20779514": "detail_used_house_20779514",
         "20862637": "detail_land_20862637"}


def fixture(name):
    return gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes()).decode("utf-8")


class FakeClient:
    """Serves the fixtures by path: area pages (the used_condo one, its links rewritten per type), search pages, and
    listing pages by nc_ id; ids in `gone` are 404s. `override` maps a path regex to a page or an exception."""

    def __init__(self, gone=(), override=None):
        self.gone, self.override, self.paths = set(gone), override or {}, []

    def get(self, path):
        self.paths.append(path)
        for pat, v in self.override.items():
            if re.search(pat, path):
                if isinstance(v, Exception):
                    raise v
                return v
        type_ = next((t for t, p in sorted(TYPES.items(), key=lambda kv: -len(kv[1])) if path.startswith(f"/{p}/")),
                     None)
        if path.endswith("/city/"):
            if type_ == "new_condo":
                return fixture("area_page_new_condo")
            return fixture("area_page_used_condo").replace("/ms/chuko/", f"/{TYPES[type_]}/")
        if "?pc=" in path:
            return fixture(f"list_{LISTS[type_]}_komae")
        id_ = re.search(r"/nc_(\d+)/", path).group(1)
        if id_ in self.gone or id_ not in PAGES:
            raise FileNotFoundError("404")
        return fixture(PAGES[id_])


def data_dir(tmp_path):
    """Fetched listings of every type (newest first by first_seen), plus a new_house one under /tochi/."""
    recs = []
    for t, path in TYPES.items():
        for i, (id_, day) in enumerate([("20205670", "2026-11-10"), ("20779514", "2026-11-12"),
                                        ("20862637", "2026-11-11")]):
            recs.append(rec(f"{id_}", t, url=f"https://suumo.jp/{path}/tokyo/sc_komae/nc_{id_}/", first_seen=day,
                            has_detail=True, area_code=f"1321{i}"))
        recs.append(rec("99999999", t, url=f"https://suumo.jp/{path}/tokyo/sc_komae/nc_99999999/",
                        first_seen="2026-11-14"))  # never fetched: not a candidate
    recs.append(rec("20862637", "new_house", url="https://suumo.jp/tochi/tokyo/sc_komae/nc_20862637/",
                    first_seen="2026-11-13", has_detail=True, area_code="13219"))
    write(tmp_path / "data", recs)
    return tmp_path / "data"


def test_candidates_newest_fetched_first_and_tochi_apart(tmp_path):
    data = data_dir(tmp_path)
    assert [i for i, _ in canary.candidates(data, "used_condo")] == ["20779514", "20862637", "20205670"]
    assert canary.candidates(data, "new_house", tochi=True) == [("20862637", "/tochi/tokyo/sc_komae/nc_20862637/")]
    assert all("/tochi/" not in p for _, p in canary.candidates(data, "new_house"))
    assert len(canary.candidates(data, "land")) == 3   # land's own pages are all under /tochi/


def test_every_check_passes_on_the_fixtures(tmp_path):
    client = FakeClient()
    result = canary.run(client, data_dir(tmp_path), log=QUIET)
    assert result["search_ok"] and result["list_ok"] and result["reachable"], result["lines"]
    # per type: area page, search page, two listing pages; + the one /tochi/ page there is
    assert len(client.paths) == 5 * 4 + 1
    assert all("sort=" not in p and "/jj/" not in p for p in client.paths)  # robots.txt
    out = tmp_path / "out"
    out.write_text("x=1\n")
    canary.write_output(result, out)
    assert out.read_text() == "x=1\nsearch_ok=true\nlist_ok=true\nreachable=true\n"


def test_the_new_condo_area_page():
    areas = parse_areas(fixture("area_page_new_condo"), TYPES["new_condo"], "tokyo")
    assert len(areas) == 43 and all(a["slug"] and a["expected"] for a in areas)
    assert areas[0] == {"code": "13101", "name": "千代田区", "slug": "sc_chiyoda", "expected": 39}


def test_the_real_derive_runs_on_a_fixture(monkeypatch):
    assert canary.derive is detail.derive
    rec = extract(fixture("detail_used_condo_20205670"), "used_condo", "20205670")
    assert canary.judge(fixture("detail_used_condo_20205670"), "used_condo", "20205670") is None
    assert detail.derive(rec)["mgmt_fee"] == 146000
    monkeypatch.setattr(canary, "derive", lambda r: 1 / 0)
    assert canary.judge(fixture("detail_used_condo_20205670"), "used_condo", "20205670").startswith(
        "ZeroDivisionError")


def test_one_odd_listing_page_is_not_a_layout_change(tmp_path):
    cands = canary.candidates(data_dir(tmp_path), "used_condo")   # 20779514, 20862637, 20205670
    odd = "<html><body>odd</body></html>"
    client = FakeClient(override={"nc_20779514": odd})
    ok, msg = canary.check_listing(client, "used_condo", cands)
    assert ok and "failed ['20779514" in msg and len(client.paths) == 3
    client = FakeClient(override={"nc_20779514": odd, "nc_20862637": odd})
    ok, msg = canary.check_listing(client, "used_condo", cands)
    assert not ok and len(client.paths) == 2   # 2 failed: decided
    ok, _ = canary.check_listing(FakeClient(override={"nc_20779514": odd}), "used_condo", cands[:1])
    assert not ok   # the only page there is failed


def test_ended_listings_are_skipped(tmp_path):
    cands = canary.candidates(data_dir(tmp_path), "used_condo")
    ok, msg = canary.check_listing(FakeClient(gone={"20779514"}), "used_condo", cands)
    assert ok and "gone ['20779514']" in msg
    client = FakeClient(gone=set(PAGES))
    ok, msg = canary.check_listing(client, "used_condo", cands)
    assert not ok and len(client.paths) == 3


def test_a_listing_page_layout_change_is_bad_listings_only(tmp_path):
    result = canary.run(FakeClient(override={r"/nc_\d+/": "<html><body><p>new layout</p></body></html>"}),
                        data_dir(tmp_path), log=QUIET)
    assert result["search_ok"] and not result["list_ok"] and result["reachable"]
    assert any(line.startswith("BAD listing") and "no spec rows" in line for line in result["lines"])


def test_a_listing_page_without_coordinates_is_bad():
    page = re.sub(r"lat|lng|initIdo|initKeido", "x", fixture("detail_used_condo_20205670"))
    assert "missing ll" in canary.judge(page, "used_condo", "20205670")


def test_a_search_page_without_listings_is_bad(tmp_path):
    empty = re.sub(r"property_unit", "x", fixture("list_used_condo_komae"))
    result = canary.run(FakeClient(override={r"\?pc=": empty}), data_dir(tmp_path), log=QUIET)
    assert not result["search_ok"] and result["list_ok"]


def test_prices_new_condo_needs_one_the_others_most(tmp_path):
    html = fixture("list_used_condo_komae")
    unpriced = html.replace("販売価格", "x", 20)   # the price cell's label: ~20 of 67 lose their price
    client = FakeClient(override={r"\?pc=": unpriced})
    ok, msg = canary.check_search(client, "used_condo")
    assert not ok and "have a price" in msg
    assert canary.PRICE_SHARE["new_condo"] == 0.0
    ok, _ = canary.check_search(FakeClient(), "new_condo")
    assert ok


def test_an_outage_stops_the_canary_but_doesnt_gate(tmp_path):
    for exc in (Unavailable("503"), NetworkError("proxy")):
        client = FakeClient(override={"/city/": exc})
        result = canary.run(client, data_dir(tmp_path), log=QUIET)
        assert result == {"search_ok": True, "list_ok": True, "reachable": False, "lines": result["lines"]}
        assert len(client.paths) == 1 and result["lines"][0].startswith("DOWN search  used_condo")


def test_a_request_that_keeps_failing_is_a_bad_check_not_a_crash(tmp_path):
    result = canary.run(FakeClient(override={"": RuntimeError("failed after 2 attempts")}), data_dir(tmp_path),
                        log=QUIET)
    assert not result["search_ok"] and not result["list_ok"] and result["reachable"]


def test_no_fetched_listing_is_bad():
    assert canary.check_listing(FakeClient(), "land", []) == (False, "no fetched listing in data/")


def test_the_command_writes_the_flags_and_exits_0(tmp_path, monkeypatch, capsys):
    from suumo import cli

    data = data_dir(tmp_path)
    used = {}

    def client(**kw):
        used.update(kw)
        return FakeClient(override={r"\?pc=": "<html></html>"})

    monkeypatch.setattr(cli, "Client", client)
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["suumo", "canary", "--data", str(data), "--out", str(out)])
    cli.main()
    assert out.read_text() == "search_ok=false\nlist_ok=true\nreachable=true\n"
    assert used == {"delay": 1.0, "retries": 2}
    assert "search pages BAD, listing pages ok" in capsys.readouterr().out


def test_a_listing_without_photos_is_fine_if_another_has_some(tmp_path, monkeypatch):
    real = canary.extract
    bare = set()

    def extract_(html, type_, id_):
        rec = real(html, type_, id_)
        if id_ in bare:
            rec.pop("img", None)
        return rec

    monkeypatch.setattr(canary, "extract", extract_)
    cands = canary.candidates(data_dir(tmp_path), "used_condo")   # 20779514, 20862637, 20205670
    bare.update({"20779514", "20862637"})   # the newest two have no photos (2026-11-25: new land listings)
    client = FakeClient()
    ok, msg = canary.check_listing(client, "used_condo", cands)
    assert ok and "failed" not in msg and len(client.paths) == 3   # judged on until one shows photos
    bare.add("20205670")
    ok, msg = canary.check_listing(FakeClient(), "used_condo", cands)
    assert not ok and msg.endswith("no photos on any page")   # none at all: the photo markup changed


def test_pick_writes_the_candidates_the_plan_job_reads(tmp_path, monkeypatch):
    from suumo import cli

    data = data_dir(tmp_path)
    want = {t: canary.candidates(data, t) for t in ("used_condo", "land")}
    tochi = canary.candidates(data, "new_house", tochi=True)
    monkeypatch.setattr("sys.argv", ["suumo", "--data", str(data), "canary", "--pick"])
    cli.main()
    picked = tmp_path / "picked"   # what crawl.yml's plan job has: the candidates file, no data/<pref>/
    (picked / "canary").mkdir(parents=True)
    (picked / "canary" / "candidates.json").write_bytes((data / canary.CANDIDATES).read_bytes())
    assert {t: canary.candidates(picked, t) for t in want} == want
    assert canary.candidates(picked, "new_house", tochi=True) == tochi
    assert canary.candidates(picked, "new_condo") == canary.candidates(data, "new_condo")
