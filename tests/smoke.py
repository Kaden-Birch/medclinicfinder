import csv
from email.message import Message
import io
import json
import os
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import app
import clinics

CLINIC = '<a href="/contact">Contact</a><a href="tel:+14035550100">Call us</a>'
CONTACT = '<p>Fax: (403) 555-0101</p><a href="mailto:hello@example.org">Email</a><form><textarea></textarea></form>'
ELEMENTS = [
    {"type": "area", "id": 42},
    {"type": "node", "id": 1, "lat": 51.0, "lon": -114.0,
     "tags": {"amenity": "dentist", "name": "Élan Dental", "website": "https://example.org",
              "addr:housenumber": "4", "addr:street": "First Street"}},
    {"type": "way", "id": 2,
     "tags": {"amenity": "dentist", "name": "Élan Dental", "addr:housenumber": "4", "addr:street": "First Street"}},
    {"type": "node", "id": 3, "tags": {"healthcare": "doctor", "name": "=Medical", "contact:phone": "403-555-0110"}},
]
PLACE = {"id": 42, "label": "Calgary, Alberta, Canada"}
for failure in (clinics.SourceError("The read operation timed out"), {"elements": ELEMENTS, "remark": "timeout"}):
    attempts = []
    with patch.dict(os.environ, MEDCLINIC_OVERPASS=""), patch.object(
        clinics, "api_json", side_effect=[failure, {"elements": ELEMENTS}]
    ) as query_api:
        results = clinics.discover(PLACE, {"Dental"}, attempts.append)
    assert attempts == [1, 2]
    assert [row["name"] for row in results] == ["Élan Dental"]
    urls = [call.args[0] for call in query_api.call_args_list]
    assert all(url.startswith(server + "?") for url, server in zip(urls, clinics.OVERPASS_SERVERS))
    query = parse_qs(urlsplit(urls[-1]).query)["data"][0]
    assert "dentist" in query and "doctors" not in query

with patch.dict(os.environ, MEDCLINIC_OVERPASS=""), patch.object(
    clinics, "api_json", return_value={"elements": [ELEMENTS[0]]}
) as query_api:
    assert clinics.discover(PLACE, {"Medical"}) == []
    assert query_api.call_count == 1

requests = []
app.locations = lambda query: [PLACE]
clinics.api_json = lambda url, source, timeout: {"elements": ELEMENTS}

def fetch(url, limit):
    requests.append(url)
    headers = Message()
    if url.endswith("/robots.txt"):
        headers["Content-Type"] = "text/plain"
        return 200, headers, b"User-agent: *\nDisallow: /private"
    headers["Content-Type"] = "text/html; charset=utf-8"
    return 200, headers, (CONTACT if url.endswith("/contact") else CLINIC).encode()

clinics.public_request = fetch
with app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler) as server:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    html = urlopen(base).read().decode()
    assert app.TOKEN in html and "__LOCAL_TOKEN__" not in html
    assert urlopen(base + "/app.js").status == 200
    assert urlopen(base + "/style.css").status == 200

    def post(path, data, key=app.TOKEN):
        req = Request(base + path, json.dumps(data).encode(), {"X-Local-Token": key})
        return urlopen(req)

    try:
        post("/api/locations", {"query": "Calgary, Alberta"}, "invalid")
        raise AssertionError("Missing authorization check")
    except HTTPError as error:
        assert error.code == 403
    with post("/api/locations", {"query": "Calgary, Alberta"}) as response:
        assert json.load(response)[0]["id"] == 42
    assert post("/api/scan", {"place": 42, "kinds": ["Medical", "Dental"]}).status == 202
    for _ in range(100):
        result = json.load(urlopen(base + "/api/status"))
        if result["phase"] in {"done", "error"}:
            break
        time.sleep(0.1)
    assert result["phase"] == "done", result
    assert result["total"] == result["completed"] == 2, result
    dental = next(row for row in result["rows"] if row["type"] == "Dental")
    assert dental["fax"] == "(403) 555-0101"
    assert dental["phone"] == "+14035550100"
    assert dental["email"] == "hello@example.org"
    assert dental["contact_form"] == dental["fax_source"] == "https://example.org/contact"
    raw = urlopen(base + "/download.csv").read()
    assert raw.startswith(b"\xef\xbb\xbf")
    exported = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    assert exported[0]["Clinic name"] == "'=Medical"
    assert exported[1]["Clinic name"] == "Élan Dental"
    assert "OpenStreetMap" in exported[1]["Attribution"]
    assert requests == ["https://example.org/robots.txt", "https://example.org", "https://example.org/contact"]
    server.shutdown()

scanner = clinics.SiteScanner("example.org")
assert not scanner.allowed("https://example.org/private")
base_row = dental | {"street": "999 Ninth Avenue", "phone": "", "fax": "", "email": ""}
assert scanner.scan(base_row, shared=True)["scan_status"] == "Shared website · branch needs review"
page = clinics.Page()
page.feed('<a href="tel:4035550100">Fax: 403-555-0100</a><p>Phone: 403-555-0101</p>')
values = clinics.contacts(page)
assert set(values["phone"]) == {"4035550101"} and set(values["fax"]) == {"4035550100"}
print("PASS: local HTTP UI, lookups, discovery, dental/medical results, deduplication, crawl, branch review, CSV download")
