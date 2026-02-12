# Savannah Restaurant Map

Interactive map of restaurants and bars in Savannah, GA. Auto-updated daily from a Google Sheet.

**Live map:** [peterg4059.github.io/savannah-restaurant-map](https://peterg4059.github.io/savannah-restaurant-map/)

## How it works

1. `generate_map.py` reads restaurant data from a Google Sheet
2. Generates `index.html` with an interactive Leaflet map
3. GitHub Actions runs daily at 8 AM ET (and on manual trigger)
4. GitHub Pages serves the map

## Adding restaurants

Just add rows to the Google Sheet. The map updates automatically every morning, or trigger a manual run from the [Actions tab](../../actions).
