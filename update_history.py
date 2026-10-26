"""Prepare a public monthly summary without any model, OpenAI API or Site tool.

Run in an ordinary scheduler. Only a fully validated result replaces history.json.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
FUELS = {"Gazole", "SP95", "SP98", "E10", "E85", "GPLc"}


def paris_today(now=None):
    now = now or datetime.now(timezone.utc)
    try:
        return now.astimezone(ZoneInfo("Europe/Paris")).date()
    except ZoneInfoNotFoundError:
        # Windows Python may lack an IANA database. Current EU DST transitions
        # are the last Sundays of March/October at 01:00 UTC.
        def transition(month):
            last = date(now.year, month, 31)
            sunday = last - timedelta(days=(last.weekday() + 1) % 7)
            return datetime.combine(sunday, datetime.min.time(), timezone.utc) + timedelta(hours=1)
        offset = 2 if transition(3) <= now < transition(10) else 1
        return (now + timedelta(hours=offset)).date()


def plan(today, period):
    cutoff = today - timedelta(days=1) if period == "current" else today.replace(day=1) - timedelta(days=1)
    last = cutoff.year * 12 + cutoff.month - 1
    months = [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(last - 11, last + 1)]
    return cutoff, months


def validate(value, cutoff, months):
    if value.get("schemaVersion") != 1 or value.get("scope") != "department" or value.get("unit") != "EUR/L" or value.get("months") != months:
        raise ValueError("Unexpected history contract or rolling window")
    through = date.fromisoformat(value["dataThrough"])
    if not cutoff - timedelta(days=7) <= through <= cutoff or through.strftime("%Y-%m") != months[-1]:
        raise ValueError("Official observations do not cover the requested period")
    if value.get("observations", 0) < 10000 or not 80 <= len(value.get("departments", {})) <= 110:
        raise ValueError("Incomplete official observations")
    for dep, fuels in value["departments"].items():
        if not fuels or set(fuels) - FUELS:
            raise ValueError("Invalid department/fuel data")
        for fuel, prices in fuels.items():
            counts = value.get("stationCounts", {}).get(dep, {}).get(fuel, [])
            if len(prices) != 12 or len(counts) != 12:
                raise ValueError("Incomplete monthly series")
            for price, count in zip(prices, counts):
                if type(count) is not int or count < 0 or (price is None and count != 0) or (price is not None and (type(price) not in (float, int) or not 0 < price < 10 or count == 0)):
                    raise ValueError("Invalid price/count")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=("current", "complete"), default="current")
    parser.add_argument("--force", action="store_true", help="Recompute even when this month's result is already present")
    parser.add_argument("--today", type=date.fromisoformat, help="Reproducible Europe/Paris calendar date")
    parser.add_argument("--archive", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "history.json")
    args = parser.parse_args()
    today = args.today or paris_today()
    cutoff, months = plan(today, args.period)
    destination = args.output.resolve()
    if destination.exists():
        try:
            old = json.loads(destination.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            old = None
        if not args.force and old and old.get("period") == args.period and old.get("calculatedFor") == (months[-1] if args.period == "complete" else today.isoformat()):
            validate(old, cutoff, months)
            print("History already calculated for this period; no archive download needed.")
            return
    else:
        old = None
    work_root = ROOT / ".work"
    work_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="history-", dir=work_root) as work:
        if not Path(work).resolve().is_relative_to(work_root.resolve()):
            raise ValueError("Temporary work directory escaped the updater workspace")
        candidate = Path(work) / "candidate.json"
        command = [sys.executable, "-B", "-X", "utf8", str(ROOT / "build_history.py"), "--as-of", cutoff.isoformat(), "--format", "json", "--cache-dir", str(Path(work) / "archives"), "--output", str(candidate)]
        for archive in args.archive:
            command.extend(["--archive", str(archive.resolve())])
        subprocess.run(command, check=True, timeout=900)
        value = json.loads(candidate.read_text(encoding="utf-8"))
        validate(value, cutoff, months)
        if old and value["dataThrough"] < old.get("dataThrough", ""):
            raise ValueError("Refusing observation cutoff regression")
        value.update({"period": args.period, "calculatedFor": months[-1] if args.period == "complete" else today.isoformat(),
                      "service": {"state": "online", "lastSuccessfulUpdate": value["generatedAt"], "dataThrough": value["dataThrough"]}})
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.write("\n")
            os.replace(temporary, destination)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()
    print(json.dumps({"saved": str(destination), "through": value["dataThrough"], "months": months, "departments": len(value["departments"]), "modelTokens": 0}))


if __name__ == "__main__":
    main()
