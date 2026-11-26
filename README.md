# MedClinicFinder

MedClinicFinder is a Windows app with a browser interface. It finds mapped medical and dental clinics within a selected city boundary, checks their listed websites for published contact details, and exports a CSV you can open in Excel.

## Run on Windows

1. Install [Python 3.10 or later](https://www.python.org/downloads/windows/). No Python packages are required.
2. Download or clone this repository. If you download a ZIP, extract it first.
3. Double-click `Start-MedClinicFinder.cmd`. Your browser should open automatically. If it doesn't, open the local address printed in the launcher window.
4. Enter a city and region, for example **Calgary, Alberta**. Choose **Find location**, select a boundary, and choose the clinic types to include.
5. Select **Find clinics and scan websites**. Keep the launcher open while the scan runs. Select **Download CSV** when it finishes.

From a terminal on macOS or Linux, you can also run `python3 app.py` in the app folder.

## Results and coverage

- Each row represents a mapped clinic location. It includes its address, website, phone, fax, email, contact form if detected, date checked, scan status and source URLs where available.
- The directory comes from OpenStreetMap. Clinics that are missing or incorrectly tagged there may be absent. Municipal boundaries must be available to the map server.
- The scanner uses website URLs already listed in OpenStreetMap. It reads public HTML from the listed page and up to three linked contact or location pages on the same site. It respects robots.txt, uses request delays, and skips interactive pages, PDFs, and JavaScript-only contact details.
- Where a branch match or contact value is unclear, check the indicated source. Empty fields mean no usable value was found in the listing or checked pages. A website may still publish those details elsewhere.
- An internet connection is required. Location lookup and clinic discovery use public Nominatim and Overpass services, which may throttle requests or experience outages. For repeated or high-volume use, set `MEDCLINIC_NOMINATIM` and `MEDCLINIC_OVERPASS` to providers that permit that workload. The Nominatim value is a base URL and the Overpass value is the interpreter URL.
- Results stay in memory until the launcher closes or a new scan starts. Location lookups are cached locally for seven days. Map data is © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) under the Open Database License (ODbL). Attribution is included in the export.
