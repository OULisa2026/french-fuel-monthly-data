"""Build the small on-device departmental history from official annual ZIPs.

No third-party dependencies. ZIP/XML files remain outside the source project.
The statistic is the station-weighted monthly mean of reported prices, NOT a
daily price series, a sales-weighted mean, or a history of today's top ten.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

FUELS = {"1": "Gazole", "2": "SP95", "3": "E85", "4": "GPLc", "5": "E10", "6": "SP98"}
ROOT = Path(__file__).resolve().parent


def department(cp):
    cp = cp.zfill(5)
    if cp.startswith("20"):
        # Corsica postal codes do not contain 2A/2B. The official commune
        # boundary lookup is used on the Page; historical rows use 200xx/201xx
        # (Corse-du-Sud) versus 202xx/206xx (Haute-Corse).
        return "2A" if cp[:3] in ("200", "201") else "2B"
    return cp[:3] if cp.startswith(("97", "98")) else cp[:2]


def aggregate(paths, months, cutoff):
    totals = defaultdict(lambda: [0.0, 0])
    latest = ""
    observations = 0
    for path in paths:
        print(f"Reading {path.name}", flush=True)
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith(".xml")]
            if len(names) != 1:
                raise ValueError("Expected exactly one XML file")
            with archive.open(names[0]) as stream:
                parser = ET.iterparse(stream, events=("start", "end"))
                _, root = next(parser)
                for event, node in parser:
                    if event != "end" or node.tag != "pdv":
                        continue
                    dep = department(node.get("cp", ""))
                    by_station = defaultdict(dict)
                    for price in node.findall("prix"):
                        date = price.get("maj", "").replace(" ", "T")
                        month = date[:7]
                        fuel = FUELS.get(price.get("id"))
                        try:
                            value = float(price.get("valeur", "0"))
                        except ValueError:
                            continue
                        if fuel and month in months and date[:10] <= cutoff and 0 < value < 10:
                            # Identical timestamps are deduplicated within a station.
                            by_station[(fuel, month)][date] = value
                            latest = max(latest, date[:10])
                    for (fuel, month), prices in by_station.items():
                        bucket = totals[(dep, fuel, month)]
                        bucket[0] += sum(prices.values()) / len(prices)
                        bucket[1] += 1
                        observations += len(prices)
                    node.clear()
                    root.clear()
    output = {}
    counts = {}
    for dep, fuel, _ in totals:
        if fuel in output.get(dep, {}):
            continue
        output.setdefault(dep, {})[fuel] = []
        counts.setdefault(dep, {})[fuel] = []
        for month in months:
            total, count = totals.get((dep, fuel, month), (0, 0))
            output[dep][fuel].append(round(total / count, 4) if count else None)
            counts[dep][fuel].append(count)
    return output, counts, latest, observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--cache-dir", type=Path, default=Path(tempfile.gettempdir()) / "FrenchFuelPrices-archives")
    parser.add_argument("--archive", action="append", type=Path, help="Existing official annual ZIP; repeat for both years")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "history.js")
    parser.add_argument("--format", choices=("js", "json"), default="js")
    args = parser.parse_args()
    as_of = datetime.fromisoformat(args.as_of)
    month_index = as_of.year * 12 + as_of.month - 1
    months = [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(month_index - 11, month_index + 1)]
    years = sorted({int(m[:4]) for m in months})
    paths = args.archive or []
    if not paths:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        for year in years:
            path = args.cache_dir / f"FrenchFuelPrices-history-{year}.zip"
            url = f"https://donnees.roulez-eco.fr/opendata/annee/{year}"
            # The current-year archive changes daily; refresh it each run.
            if not path.exists() or year == as_of.year:
                print(f"Downloading {url}", flush=True)
                with urllib.request.urlopen(url, timeout=120) as response, path.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        output.write(block)
            paths.append(path)
    by_year = {}
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            xml = [name for name in archive.namelist() if name.endswith(".xml")]
        match = re.search(r"(20\d{2})\.xml$", xml[0]) if len(xml) == 1 else None
        if not match:
            raise ValueError(f"Cannot identify official archive year: {path}")
        year = int(match[1])
        if year in by_year:
            raise ValueError(f"Duplicate archive for {year}")
        by_year[year] = path
    if sorted(by_year) != years:
        raise ValueError(f"Expected annual archives {years}, received {sorted(by_year)}")
    paths = [by_year[year] for year in years]
    archive_records = []
    for year, path in zip(years, paths):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        archive_records.append({"url": f"https://donnees.roulez-eco.fr/opendata/annee/{year}", "sha256": digest})
    deps, counts, latest, observations = aggregate(paths, months, args.as_of)
    if not deps or not latest:
        raise ValueError("No usable official observations; refusing to write an empty history")
    result = {"schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(), "dataThrough": latest,
              "months": months, "unit": "EUR/L", "scope": "department",
              "method": "Equal-weight mean of station monthly means of reported price observations. No carry-forward or interpolation. Current month is partial.",
              "source": "DGCCRF / prix-carburants.gouv.fr", "license": "Licence Ouverte 2.0",
              "sourceUrl": "https://www.prix-carburants.gouv.fr/rubrique/opendata/",
              "archives": archive_records,
              "observations": observations, "departments": deps, "stationCounts": counts}
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    text = payload + "\n" if args.format == "json" else "// Generated from official annual archives. See tools/build_history.py.\nmodule.exports = " + payload + ";\n"
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote {args.output}: {len(deps)} departments, {observations} observations, through {latest}, {len(text.encode('utf-8'))} bytes", flush=True)


if __name__ == "__main__":
    main()
