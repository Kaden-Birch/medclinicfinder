"""Local browser interface and background scan jobs."""
import csv
import io
import json
import os
import secrets
import threading
import traceback
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from clinics import SiteScanner, SourceError, discover, host_key, locations

WEB = Path(__file__).resolve().parent / "web"
LOCK = threading.Lock()
PLACES = {}
JOB = None
TOKEN = secrets.token_urlsafe(24)
COLUMNS = {
    "name": "Clinic name", "type": "Clinic type", "location": "Search location", "address": "Address",
    "website": "Website", "phone": "Phone", "fax": "Fax", "email": "Email",
    "contact_form": "Contact form", "scan_status": "Scan status", "checked": "Date checked",
    "phone_source": "Phone source", "fax_source": "Fax source", "email_source": "Email source",
    "listing_source": "Clinic listing", "latitude": "Latitude", "longitude": "Longitude", "attribution": "Attribution",
}


def safe_cell(value):
    # Keep formulas inert and preserve leading plus signs in international contact numbers.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    if isinstance(value, str) and value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return "" if value is None else value


def export_csv(rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS.values())
    for row in rows:
        writer.writerow([safe_cell(row.get(column)) for column in COLUMNS])
    return buffer.getvalue().encode("utf-8-sig")


def update(job, **values):
    with LOCK:
        job.update(values)


def scan_group(group, host, job):
    scanner = SiteScanner(host)
    for original in group:
        result = scanner.scan(original, shared=len(group) > 1)
        with LOCK:
            job["rows"].append(result)
            job["completed"] += 1


def run_scan(job, place, kinds):
    try:
        rows = discover(place, kinds)
        groups = {}
        for row in rows:
            groups.setdefault(host_key(row["website"]), []).append(row)
        update(job, phase="scanning", total=len(rows))
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(scan_group, group, host, job) for host, group in groups.items()]
            for future in as_completed(futures):
                future.result()
        with LOCK:
            job["rows"].sort(key=lambda row: (row["name"].casefold(), row["address"]))
            job["phase"] = "done"
    except SourceError as error:
        update(job, phase="error", error=str(error))
    except Exception:
        traceback.print_exc()
        update(job, phase="error", error="The scan stopped unexpectedly. See the launcher window for details.")


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, content, content_type, **headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(content)

    def json(self, status, body):
        self.reply(status, json.dumps(body).encode(), "application/json; charset=utf-8")

    def check_host(self):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(403)
            return False
        return True

    def do_GET(self):
        if not self.check_host():
            return
        assets = {
            "/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
        }
        if self.path in assets:
            name, mime = assets[self.path]
            body = (WEB / name).read_bytes().replace(b"__LOCAL_TOKEN__", TOKEN.encode())
            self.reply(200, body, mime + "; charset=utf-8")
            return
        if self.path not in {"/api/status", "/download.csv"}:
            self.send_error(404)
            return
        with LOCK:
            job = dict(JOB, rows=list(JOB["rows"])) if JOB else None
        if self.path == "/api/status":
            if job and job["phase"] != "done":
                job.pop("rows")
            self.json(200, job)
        elif job and job["phase"] == "done" and job["rows"]:
            self.reply(200, export_csv(job["rows"]), "text/csv; charset=utf-8",
                       Content_Disposition='attachment; filename="medclinicfinder.csv"')
        else:
            self.send_error(404)

    def do_POST(self):
        global JOB
        if not self.check_host():
            return
        if self.headers.get("X-Local-Token") != TOKEN:
            self.json(403, {"error": "Reload the app and try again."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            self.json(400, {"error": "Invalid request."})
            return
        if self.path == "/api/locations":
            query = body.get("query")
            if not isinstance(query, str) or not 2 <= len(query.strip()) <= 140:
                self.json(400, {"error": "Enter a city and region."})
                return
            try:
                matches = locations(query.strip())
            except SourceError as error:
                self.json(502, {"error": str(error)})
                return
            with LOCK:
                PLACES.update({place["id"]: place for place in matches})
            self.json(200, matches)
            return
        if self.path != "/api/scan":
            self.send_error(404)
            return
        kinds = body.get("kinds")
        place_id = body.get("place")
        if (type(place_id) is not int or not isinstance(kinds, list) or not kinds
                or any(kind not in ("Medical", "Dental") for kind in kinds)):
            self.json(400, {"error": "Select a location and at least one clinic type."})
            return
        with LOCK:
            place = PLACES.get(place_id)
            busy = JOB and JOB["phase"] in {"discovering", "scanning"}
            if place and not busy:
                job = {"phase": "discovering", "place": place["label"], "kinds": kinds,
                       "completed": 0, "total": 0, "rows": []}
                JOB = job
        if not place:
            self.json(400, {"error": "Look up and select a location first."})
        elif busy:
            self.json(409, {"error": "A clinic scan is already running."})
        else:
            threading.Thread(target=run_scan, args=(job, place, kinds), daemon=True).start()
            self.json(202, {"started": True})

    def log_message(self, format, *args):
        # Avoid recording location searches or routine progress polling.
        pass


def main():
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        address = f"http://127.0.0.1:{server.server_port}/"
        print(f"MedClinicFinder is running at {address}", flush=True)
        print("Keep this window open while using the app. Press Ctrl+C to stop.", flush=True)
        if os.environ.get("MEDCLINIC_NO_BROWSER") != "1":
            threading.Timer(0.25, webbrowser.open, args=(address,)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
