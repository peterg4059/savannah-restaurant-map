#!/usr/bin/env python3
"""
Reads Savannah restaurant data from a Google Sheet and generates
an interactive Leaflet map (index.html) with color-coded markers.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request

import gspread
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from oauth2client.service_account import ServiceAccountCredentials

# ── Config ──────────────────────────────────────────────────────────
SPREADSHEET_ID = "1Lat0eMctYh7XL4YrVFR9gC_OF4pCzjBKnscb_S9Sv0c"
SHEET_NAME = "Full Data"
SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
LOCAL_CREDS_PATH = (
    "/Users/peter/Library/Mobile Documents/com~apple~CloudDocs/"
    "iCloud Downloads/trading-strategies-484022-17f846591b01.json"
)

# Columns (0-indexed): A=0 Name, B=1 Location, C=2 Type, E=4 Summary,
#                       F=5 Picture, O=14 Address
COL_NAME = 0
COL_LOCATION = 1
COL_TYPE = 2
COL_SUMMARY = 4
COL_ADDRESS = 14

# ── Marker categories ──────────────────────────────────────────────
# Each tuple: (display_label, marker_color, icon)
CATEGORIES = {
    "restaurant": ("Restaurant", "#C62828", "utensils"),       # red
    "bar":        ("Bar",        "#1565C0", "wine-glass-alt"), # dark blue
    "rooftop":    ("Rooftop Bar","#0097A7", "cocktail"),       # teal
    "other":      ("Other",      "#2E7D32", "store"),          # green
}


def classify(type_str: str) -> str:
    """Classify a column-C type string into one of 4 categories."""
    t = type_str.strip().lower()
    if t == "rooftop bar":
        return "rooftop"
    if "bar" in t and "restaurant" not in t and "food" not in t:
        return "bar"
    if t in ("restaurant", "lunch"):
        return "restaurant"
    if "bar" in t and ("restaurant" in t or "food" in t):
        # "Bar + Restaurant", "Bar + Food", "Bar + Foodish"
        return "bar"
    if t == "restaurant":
        return "restaurant"
    # Food Hall, Bakery, Food Truck, empty, etc.
    return "other"


def get_credentials():
    """Get Google credentials from env var or local file."""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
        if "private_key" in creds_dict and isinstance(creds_dict["private_key"], str):
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        return ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPES)
    if os.path.exists(LOCAL_CREDS_PATH):
        return ServiceAccountCredentials.from_json_keyfile_name(LOCAL_CREDS_PATH, SCOPES)
    raise ValueError("No Google Sheets credentials found.")


def get_image_formulas(creds, row_count: int) -> dict[int, str]:
    """
    Use Sheets API v4 directly to read =IMAGE() formulas from column F,
    since gspread returns empty strings for image formulas.
    Returns {row_index (0-based data row): image_url}.
    """
    access_token = creds.get_access_token().access_token
    encoded_range = urllib.parse.quote(f"{SHEET_NAME}!F2:F{row_count + 1}")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
        f"/values/{encoded_range}?valueRenderOption=FORMULA"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())

    image_urls = {}
    pattern = re.compile(r'=\s*[Ii][Mm][Aa][Gg][Ee]\s*\(\s*"([^"]+)"\s*\)', re.IGNORECASE)
    for i, row in enumerate(data.get("values", [])):
        if row:
            m = pattern.match(row[0])
            if m:
                image_urls[i] = m.group(1)
    return image_urls


def fetch_sheet_data():
    """Pull restaurant rows from the Google Sheet."""
    creds = get_credentials()
    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)

    all_rows = ws.get_all_values()
    data_rows = all_rows[1:]  # skip header

    # Get image formulas separately
    image_urls = get_image_formulas(creds, len(data_rows))

    restaurants = []
    for i, row in enumerate(data_rows):
        location = row[COL_LOCATION] if len(row) > COL_LOCATION else ""
        if "sav" not in location.lower():
            continue

        name = row[COL_NAME] if len(row) > COL_NAME else ""
        rtype = row[COL_TYPE] if len(row) > COL_TYPE else ""
        summary = row[COL_SUMMARY] if len(row) > COL_SUMMARY else ""
        address = row[COL_ADDRESS] if len(row) > COL_ADDRESS else ""

        if not name or not address:
            continue

        photo_url = image_urls.get(i, "")

        restaurants.append({
            "name": name,
            "type": rtype,
            "category": classify(rtype),
            "summary": summary,
            "address": address,
            "photo_url": photo_url,
        })

    print(f"Fetched {len(restaurants)} SAV restaurants from sheet.")
    return restaurants


def geocode_restaurants(restaurants: list[dict]) -> list[dict]:
    """Add lat/lng to each restaurant using Nominatim geocoder."""
    geolocator = Nominatim(user_agent="savannah-restaurant-map")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.1)

    # Cache file to avoid re-geocoding
    cache_path = os.path.join(os.path.dirname(__file__) or ".", "geocode_cache.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)

    for r in restaurants:
        addr = r["address"]
        if addr in cache:
            r["lat"] = cache[addr]["lat"]
            r["lng"] = cache[addr]["lng"]
            continue

        try:
            loc = geocode(addr)
            if loc:
                r["lat"] = loc.latitude
                r["lng"] = loc.longitude
                cache[addr] = {"lat": loc.latitude, "lng": loc.longitude}
                print(f"  Geocoded: {r['name']} -> ({loc.latitude:.5f}, {loc.longitude:.5f})")
            else:
                print(f"  WARNING: Could not geocode '{addr}' for {r['name']}")
                r["lat"] = None
                r["lng"] = None
        except Exception as e:
            print(f"  ERROR geocoding '{addr}': {e}")
            r["lat"] = None
            r["lng"] = None

    # Save cache
    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)

    # Filter out any that failed geocoding
    before = len(restaurants)
    restaurants = [r for r in restaurants if r.get("lat") is not None]
    if before != len(restaurants):
        print(f"  Dropped {before - len(restaurants)} restaurants with no coordinates.")
    return restaurants


def generate_html(restaurants: list[dict], output_path: str = "index.html"):
    """Generate the Leaflet map HTML file."""

    # Build the JavaScript data array
    markers_js = json.dumps(restaurants, indent=2)

    # Category config for JS
    cat_config_js = json.dumps(
        {k: {"label": v[0], "color": v[1], "icon": v[2]} for k, v in CATEGORIES.items()},
        indent=2,
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Savannah Restaurant Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" />
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  #map {{ width: 100vw; height: 100vh; }}

  .legend {{
    background: white;
    padding: 12px 16px;
    border-radius: 10px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.2);
    font-size: 13px;
    line-height: 1.8;
  }}
  .legend h4 {{
    margin-bottom: 6px;
    font-size: 14px;
    color: #333;
  }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    opacity: 1;
    transition: opacity 0.2s;
  }}
  .legend-item.hidden {{
    opacity: 0.35;
  }}
  .legend-dot {{
    width: 14px;
    height: 14px;
    border-radius: 50%;
    flex-shrink: 0;
  }}

  .custom-popup .leaflet-popup-content-wrapper {{
    border-radius: 12px;
    padding: 0;
    overflow: hidden;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
  }}
  .custom-popup .leaflet-popup-content {{
    margin: 0;
    min-width: 240px;
    max-width: 300px;
  }}
  .popup-photo {{
    width: 100%;
    height: 160px;
    object-fit: cover;
    display: block;
  }}
  .popup-body {{
    padding: 12px 14px;
  }}
  .popup-name {{
    font-size: 16px;
    font-weight: 700;
    color: #222;
    margin-bottom: 4px;
  }}
  .popup-type {{
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
    color: white;
    margin-bottom: 8px;
  }}
  .popup-summary {{
    font-size: 13px;
    color: #555;
    line-height: 1.4;
    margin-bottom: 10px;
  }}
  .popup-nav {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #1a73e8;
    color: white;
    text-decoration: none;
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    transition: background 0.2s;
  }}
  .popup-nav:hover {{ background: #1557b0; }}

  .marker-icon {{
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    color: white;
    font-size: 14px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    border: 2px solid white;
  }}
</style>
</head>
<body>
<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const RESTAURANTS = {markers_js};
const CATEGORIES = {cat_config_js};

// Initialize map centered on Savannah
const map = L.map('map', {{
  zoomControl: true,
  attributionControl: false
}}).setView([32.0809, -81.0912], 13);

L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
}}).addTo(map);

// Create layer groups per category
const layerGroups = {{}};
Object.keys(CATEGORIES).forEach(k => {{
  layerGroups[k] = L.layerGroup().addTo(map);
}});

// Custom icon factory
function makeIcon(cat) {{
  const cfg = CATEGORIES[cat] || CATEGORIES['other'];
  const div = document.createElement('div');
  return L.divIcon({{
    className: '',
    html: `<div class="marker-icon" style="background:${{cfg.color}};width:32px;height:32px;">
             <i class="fas fa-${{cfg.icon}}"></i>
           </div>`,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -18]
  }});
}}

// Add markers
RESTAURANTS.forEach(r => {{
  const cfg = CATEGORIES[r.category] || CATEGORIES['other'];
  const gmapsUrl = 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(r.address);

  let photoHtml = '';
  if (r.photo_url) {{
    photoHtml = `<img class="popup-photo" src="${{r.photo_url}}" alt="${{r.name}}" onerror="this.style.display='none'" />`;
  }}

  let summaryHtml = '';
  if (r.summary) {{
    summaryHtml = `<div class="popup-summary">${{r.summary}}</div>`;
  }}

  const popup = `
    ${{photoHtml}}
    <div class="popup-body">
      <div class="popup-name">${{r.name}}</div>
      <span class="popup-type" style="background:${{cfg.color}}">${{r.type || cfg.label}}</span>
      ${{summaryHtml}}
      <a class="popup-nav" href="${{gmapsUrl}}" target="_blank">
        <i class="fas fa-directions"></i> Open in Google Maps
      </a>
    </div>
  `;

  const marker = L.marker([r.lat, r.lng], {{ icon: makeIcon(r.category) }})
    .bindPopup(popup, {{ className: 'custom-popup', maxWidth: 300 }});

  layerGroups[r.category].addLayer(marker);
}});

// Legend with toggle
const legend = L.control({{ position: 'bottomright' }});
legend.onAdd = function() {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = '<h4>Savannah Eats & Drinks</h4>';

  Object.entries(CATEGORIES).forEach(([key, cfg]) => {{
    const count = RESTAURANTS.filter(r => r.category === key).length;
    if (count === 0) return;
    const item = document.createElement('div');
    item.className = 'legend-item';
    item.innerHTML = `<span class="legend-dot" style="background:${{cfg.color}}"></span> ${{cfg.label}} (${{count}})`;
    item.addEventListener('click', () => {{
      if (map.hasLayer(layerGroups[key])) {{
        map.removeLayer(layerGroups[key]);
        item.classList.add('hidden');
      }} else {{
        map.addLayer(layerGroups[key]);
        item.classList.remove('hidden');
      }}
    }});
    div.appendChild(item);
  }});

  L.DomUtil.disableClickPropagation(div);
  return div;
}};
legend.addTo(map);

// Fit bounds to all markers
const allCoords = RESTAURANTS.map(r => [r.lat, r.lng]);
if (allCoords.length) {{
  map.fitBounds(allCoords, {{ padding: [30, 30] }});
}}
</script>
</body>
</html>"""

    out_path = os.path.join(os.path.dirname(__file__) or ".", output_path)
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Generated {out_path} with {len(restaurants)} markers.")


def generate_kml(restaurants: list[dict], output_path: str = "map.kml"):
    """Generate a KML file for import into Google My Maps.

    Uses ExtendedData with a 'Category' column so My Maps can use
    'Style by data column' for persistent, mobile-friendly icons.
    """

    def esc(text: str) -> str:
        """Escape XML special characters."""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))

    # Map category keys to friendly labels for the data column
    CAT_LABELS = {
        "restaurant": "Restaurant",
        "bar": "Bar",
        "rooftop": "Rooftop Bar",
        "other": "Other",
    }

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        '<Document>',
        '<name>Savannah Restaurants &amp; Bars</name>',
        '<description>Auto-generated from Google Sheets</description>',
    ]

    # Schema for ExtendedData columns (My Maps reads these as data columns)
    lines.append('<Schema id="restaurant_schema">')
    lines.append('  <SimpleField type="string" name="Category"><displayName>Category</displayName></SimpleField>')
    lines.append('  <SimpleField type="string" name="Address"><displayName>Address</displayName></SimpleField>')
    lines.append('</Schema>')

    for r in restaurants:
        gmaps_url = (
            "https://www.google.com/maps/search/?api=1&query="
            + urllib.parse.quote(r["address"])
        )

        cat_label = CAT_LABELS.get(r["category"], "Other")

        # Build description with summary + photo so the field is never empty
        desc_parts = []
        if r.get("summary"):
            desc_parts.append(esc(r["summary"]))
        if r.get("photo_url"):
            desc_parts.append(f'<img src="{esc(r["photo_url"])}" width="300" />')
        description = "<br/>".join(desc_parts) if desc_parts else esc(r["name"])

        lines.append("<Placemark>")
        lines.append(f"  <name>{esc(r['name'])}</name>")
        lines.append(f"  <description><![CDATA[{description}]]></description>")
        lines.append('  <ExtendedData><SchemaData schemaUrl="#restaurant_schema">')
        lines.append(f'    <SimpleData name="Category">{esc(cat_label)}</SimpleData>')
        lines.append(f'    <SimpleData name="Address">{esc(r["address"])}</SimpleData>')
        lines.append('  </SchemaData></ExtendedData>')
        lines.append("  <Point>")
        lines.append(f"    <coordinates>{r['lng']},{r['lat']},0</coordinates>")
        lines.append("  </Point>")
        lines.append("</Placemark>")

    lines.append("</Document>")
    lines.append("</kml>")

    out_path = os.path.join(os.path.dirname(__file__) or ".", output_path)
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Generated {out_path} with {len(restaurants)} placemarks.")


def main():
    restaurants = fetch_sheet_data()
    restaurants = geocode_restaurants(restaurants)
    generate_html(restaurants)
    generate_kml(restaurants)


if __name__ == "__main__":
    main()
