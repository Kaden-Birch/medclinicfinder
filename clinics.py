"""OpenStreetMap discovery and contact extraction from public clinic pages."""
import http.client
import ipaddress
import json
import os
import re
import socket
import threading
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

USER_AGENT = "MedClinicFinder/1.0 (+https://github.com/Kaden-Birch/medclinicfinder)"
ATTRIBUTION = "© OpenStreetMap contributors (ODbL): https://www.openstreetmap.org/copyright"
NOMINATIM = os.environ.get("MEDCLINIC_NOMINATIM", "https://nominatim.openstreetmap.org")
OVERPASS = os.environ.get("MEDCLINIC_OVERPASS", "https://overpass-api.de/api/interpreter")
CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "MedClinicFinder"
LOOKUP_LOCK = threading.Lock()
OSM_FIELDS = ("phone", "fax", "email")
EMAIL = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+", re.I)
NUMBER = re.compile(r"(?<![\w+])(?:\+\d[\d .()-]{6,23}|(?:1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4})(?!\d)")
CONTACT_LINK = re.compile(r"contact|location|find.us|reach.us|coordonn|kontakt|contacto", re.I)
BRANCH_LINK = re.compile(r"(?:^|[-/_ ])(?:our[-_ ]|all[-_ ])?locations(?:$|[-/_ .])", re.I)
PHONE_LABEL = r"(?:phone|tel(?:ephone)?|call)"
FAX_LABEL = r"(?:fax|facsimile)"
EXPECTED_ERRORS = (ValueError, OSError, http.client.HTTPException, UnicodeError)


class SourceError(Exception):
    pass


def api_json(url, source, timeout):
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            data = response.read(12_000_001)
        if len(data) > 12_000_000:
            raise SourceError("The result is too large. Choose a smaller area.")
        return json.loads(data)
    except (URLError, OSError, ValueError, http.client.HTTPException) as error:
        raise SourceError(f"{source} is unavailable ({error}). Try again in a moment.") from error


def locations(query):
    key = " ".join(query.casefold().split())
    with LOOKUP_LOCK:
        path = CACHE / "locations.json"
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        if key in cache and time.time() - cache[key]["time"] < 7 * 86400:
            return cache[key]["results"]
        params = urlencode({"q": query, "format": "jsonv2", "limit": 12, "addressdetails": 1})
        try:
            payload = api_json(f"{NOMINATIM.rstrip('/')}/search?{params}", "Location search", 15)
        finally:
            # Public Nominatim permits at most one request per second.
            time.sleep(1)
        results = [
            {"id": int(item["osm_id"]), "label": item["display_name"]}
            for item in payload
            if item.get("osm_type") == "relation"
            and item.get("category") in {"boundary", "place"}
            and item.get("addresstype") not in {"country", "state", "province", "continent"}
        ]
        cache[key] = {"time": time.time(), "results": results}
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(list(cache.items())[-200:])), encoding="utf-8")
        except OSError:
            pass
        return results


def website_url(value):
    value = value.partition(";")[0].strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"https", "http"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 443 if parts.scheme == "https" else 80}
                or re.search(r"[\\\s\x00-\x1f\x7f]", parts.netloc)):
            return ""
        return urlunsplit(parts._replace(fragment=""))
    except ValueError:
        return ""


def norm(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def host_key(url):
    return (urlsplit(url).hostname or "").casefold().rstrip(".").removeprefix("www.")


def clinic_rows(elements, kinds, location):
    rows = []
    seen = set()
    for item in sorted(elements, key=lambda item: (
        -sum(bool(item.get("tags", {}).get(key)) for key in ("website", "contact:website", "addr:street", "addr:housenumber")),
        item["id"],
    )):
        tags = item.get("tags", {})
        kind = "Dental" if "dentist" in {tags.get("amenity"), tags.get("healthcare")} else "Medical"
        name = tags.get("name") or tags.get("brand") or tags.get("operator") or ""
        if kind not in kinds or not name.strip():
            continue
        street = " ".join(filter(None, (tags.get("addr:housenumber"), tags.get("addr:street"))))
        address = ", ".join(filter(None, (street, tags.get("addr:unit"), tags.get("addr:city"), tags.get("addr:postcode"))))
        point = item.get("center", item)
        lat, lon = point.get("lat"), point.get("lon")
        if street and tags.get("addr:housenumber") and tags.get("addr:street"):
            key = (kind, norm(name), norm(street), norm(tags.get("addr:unit", "")))
        elif lat is not None and lon is not None:
            key = (kind, norm(name), round(lat, 4), round(lon, 4))
        else:
            key = (item["type"], item["id"])
        if key in seen:
            continue
        seen.add(key)
        source = f"https://www.openstreetmap.org/{item['type']}/{item['id']}"
        row = {
            "name": name, "type": kind, "location": location, "address": address, "street": street,
            "postcode": tags.get("addr:postcode", ""), "website": website_url(tags.get("contact:website") or tags.get("website") or ""),
            "latitude": lat, "longitude": lon, "listing_source": source, "checked": "", "contact_form": "",
            "scan_status": "Not scanned", "attribution": ATTRIBUTION,
        }
        for field in OSM_FIELDS:
            row[field] = tags.get(f"contact:{field}") or tags.get(field) or ""
            row[field + "_source"] = source if row[field] else ""
        rows.append(row)
    return sorted(rows, key=lambda row: (row["name"].casefold(), row["address"]))


def discover(place, kinds):
    relation = int(place["id"])
    # Include the area itself in the response so an unmapped boundary is distinguishable from zero clinics.
    query = (
        '[out:json][timeout:45];'
        f'rel({relation});map_to_area->.city;.city out ids;'
        '(nwr["amenity"~"^(clinic|doctors|dentist)$"](area.city);'
        'nwr["healthcare"~"^(clinic|doctor|dentist)$"](area.city););out center;'
    )
    data = api_json(f"{OVERPASS}?{urlencode({'data': query})}", "Clinic discovery", 60)
    if data.get("remark"):
        raise SourceError("The map server returned incomplete results. Retry or select a smaller area.")
    elements = data.get("elements", [])
    if not any(item["type"] == "area" for item in elements):
        raise SourceError("The map server has no searchable boundary for this place. Try a nearby municipality.")
    return clinic_rows([item for item in elements if item["type"] != "area"], kinds, place["label"])


def public_request(url, limit):
    if not website_url(url):
        raise ValueError("Unsupported URL")
    parts = urlsplit(url)
    host = parts.hostname.encode("idna").decode("ascii")
    port = 443 if parts.scheme == "https" else 80
    addresses = list(dict.fromkeys(result[4][0] for result in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
        raise ValueError("Non-public website address")
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = cls(host, port, timeout=6)
    # Use the validated address for the connection and the original hostname for TLS verification.
    connection._create_connection = lambda _address, timeout, source_address: socket.create_connection(
        (addresses[0], port), timeout, source_address
    )
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    try:
        connection.request("GET", target, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,*/*;q=0.1"})
        response = connection.getresponse()
        body = response.read(limit + 1) if response.status == 200 else b""
        if len(body) > limit:
            raise ValueError("Page exceeds scan size")
        return response.status, response.headers, body
    finally:
        connection.close()


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = []
        self.links = []
        self.hidden = None
        self.anchor = None
        self.has_form = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript"}:
            self.hidden = tag
        if self.hidden:
            return
        attrs = dict(attrs)
        if tag == "textarea":
            self.has_form = True
        if tag == "a" and attrs.get("href"):
            self.anchor = [attrs["href"], []]
            self.links.append(self.anchor)
        if tag in {"br", "p", "div", "li", "tr", "section", "address", "footer", "header", "h1", "h2", "h3"}:
            self.lines.append("\n")

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden = None
        elif not self.hidden:
            if tag == "a":
                self.anchor = None
            if tag in {"p", "div", "li", "tr", "section", "address", "footer", "header", "h1", "h2", "h3"}:
                self.lines.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.lines.append(re.sub(r"\s+", " ", data))
            if self.anchor:
                self.anchor[1].append(data)

    @property
    def text(self):
        return "".join(self.lines)


def contacts(page):
    fields = {field: {} for field in OSM_FIELDS}

    def add(field, value):
        if field == "email":
            key = value.casefold()
            if key.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")):
                return
        else:
            key = re.sub(r"\D", "", value)
            if not 7 <= len(key) <= 15:
                return
            if len(key) == 11 and key.startswith("1"):
                key = key[1:]
        fields[field].setdefault(key, value)

    for href, _label in page.links:
        scheme, _, value = href.partition(":")
        value = unquote(value.partition("?")[0])
        if scheme.casefold() == "mailto":
            for email in EMAIL.findall(value):
                add("email", email)
        elif scheme.casefold() in {"tel", "fax"}:
            number = re.match(r"\+?[\d(). -]+", value)
            if number:
                add("fax" if scheme.casefold() == "fax" else "phone", number[0].strip())
    for email in EMAIL.findall(page.text):
        add("email", email)
    for match in NUMBER.finditer(page.text):
        prefix = page.text[max(0, match.start() - 45):match.start()]
        label = re.search(fr"\b({FAX_LABEL}|{PHONE_LABEL})[\s:：.#-]*$", prefix, re.I)
        if label:
            add("fax" if re.fullmatch(FAX_LABEL, label[1], re.I) else "phone", match[0])
    for key in fields["fax"]:
        fields["phone"].pop(key, None)
    return fields


class SiteScanner:
    def __init__(self, host):
        self.host = host
        self.robots = {}
        self.pages = {}
        self.last_request = 0
        self.delay = 0.8

    def request(self, url, limit):
        time.sleep(max(0, self.last_request + self.delay - time.monotonic()))
        try:
            return public_request(url, limit)
        finally:
            self.last_request = time.monotonic()

    def allowed(self, url):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            try:
                for _ in range(4):
                    status, headers, body = self.request(robots_url, 512_000)
                    if status not in {301, 302, 303, 307, 308}:
                        break
                    target = website_url(urljoin(robots_url, headers.get("Location", "")))
                    if not target or host_key(target) != self.host:
                        break
                    robots_url = target
                if status == 200:
                    parser = RobotFileParser()
                    parser.parse(body.decode("utf-8", errors="replace").splitlines())
                    delay = parser.crawl_delay("MedClinicFinder") or 0
                    rate = parser.request_rate("MedClinicFinder")
                    if rate and rate.requests:
                        delay = max(delay, rate.seconds / rate.requests)
                    self.delay = max(self.delay, delay)
                    self.robots[origin] = parser if self.delay <= 15 else False
                else:
                    self.robots[origin] = status in {404, 410}
            except EXPECTED_ERRORS:
                self.robots[origin] = False
        rules = self.robots[origin]
        return rules.can_fetch("MedClinicFinder", url) if isinstance(rules, RobotFileParser) else rules

    def page(self, url):
        if url not in self.pages:
            self.pages[url] = self.load_page(url)
        return self.pages[url]

    def load_page(self, url):
        for _ in range(4):
            if not self.allowed(url):
                return None, "Website scan restricted or unavailable"
            try:
                status, headers, body = self.request(url, 1_500_000)
            except EXPECTED_ERRORS:
                return None, "Website unreachable"
            if status in {301, 302, 303, 307, 308}:
                url = website_url(urljoin(url, headers.get("Location", "")))
                if not url or host_key(url) != self.host:
                    return None, "Website redirects to another domain"
                continue
            if status != 200 or headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                return None, f"HTML page unavailable (HTTP {status})"
            page = Page()
            try:
                page.feed(body.decode(headers.get_content_charset() or "utf-8", errors="replace"))
                page.close()
            except LookupError:
                page.feed(body.decode("utf-8", errors="replace"))
                page.close()
            return (url, page), ""
        return None, "Too many website redirects"

    def scan(self, original, shared):
        row = dict(original, checked=date.today().isoformat())
        if not row["website"]:
            return dict(row, scan_status="No website in listing")
        start, reason = self.page(row["website"])
        parts = urlsplit(row["website"])
        if start is None and parts.path not in {"", "/"}:
            start, reason = self.page(urlunsplit(parts._replace(path="/", query="")))
        if start is None:
            return dict(row, scan_status=reason)

        url, page = start
        links = []
        for href, label in page.links:
            description = href + " " + " ".join(label)
            if href.startswith("#") or not CONTACT_LINK.search(description):
                continue
            try:
                target = website_url(urljoin(url, href))
            except ValueError:
                continue
            if not target or host_key(target) != self.host:
                continue
            if target != url and target not in links:
                links.append(target)
            if BRANCH_LINK.search(urlsplit(target).path) or BRANCH_LINK.search(" ".join(label)):
                shared = True
        pages = [start]
        for target in links[:3]:
            found, _ = self.page(target)
            if found and found[0] not in {item[0] for item in pages}:
                pages.append(found)

        street = norm(row["street"])
        postcode = norm(row["postcode"])
        matched = [
            (source, candidate) for source, candidate in pages
            if (street and re.search(r"\d", street) and f" {street} " in f" {norm(candidate.text)} ")
            or (not street and postcode and f" {postcode} " in f" {norm(candidate.text)} ")
        ]
        eligible = matched if shared else pages
        candidates = {field: {} for field in OSM_FIELDS}
        for source, candidate in eligible:
            for field, values in contacts(candidate).items():
                for key, value in values.items():
                    candidates[field].setdefault(key, (value, source))
            if not row["contact_form"] and CONTACT_LINK.search(urlsplit(source).path):
                # Detect an actual form, not merely a page named Contact.
                if candidate.has_form:
                    row["contact_form"] = source
        ambiguous = False
        for field, values in candidates.items():
            if len(values) == 1:
                row[field], row[field + "_source"] = next(iter(values.values()))
            elif len(values) > 1:
                ambiguous = True
        if shared and not matched:
            row["scan_status"] = "Shared website · branch needs review"
        elif ambiguous:
            row["scan_status"] = "Multiple contacts · review website"
        elif any(row[field + "_source"] and row[field + "_source"] != row["listing_source"] for field in OSM_FIELDS) or row["contact_form"]:
            row["scan_status"] = "Contact found on website"
        else:
            row["scan_status"] = "No contact found on scanned pages"
        return row
