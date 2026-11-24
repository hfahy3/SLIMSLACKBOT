#!/usr/bin/env python3
"""
SLIM Slack Bot (demo version)
=============================

Hourly station-health reporter for a warehouse floor. The original version scraped an internal dashboard that listed
every workstation and the status of its connected devices (video, DMXX scanner, standard scanner, keypad, packing-slip
printer). This demo version keeps the full pipeline but replaces the real website with SYNTHETIC data, so it runs
anywhere, needs no login, and contains no company data.

Pipeline
--------
1. Fetch      get the dashboard page (here: a generated HTML page; in production: an HTTP request to the real site)
2. Parse      turn the HTML table into one record per station with a pass/fail flag per device
3. Evaluate   health % per group of stations = stations with no device defects / stations in the group
4. Report     groups at/above the 90% standard get a green check; groups below it get an alert, followed by the exact
              stations and defect types so people know where to go fix things
5. Deliver    print to the console, or post to Slack through an incoming webhook (standard library only)
6. Repeat     once an hour (--loop)

Usage
-----
  python3 slim_bot.py --demo                 three simulated hourly reports (deterministic sample output)
  python3 slim_bot.py --once                 one report using the current time as the random seed
  python3 slim_bot.py --loop                 report every hour until stopped
  python3 slim_bot.py --once --webhook URL   also post the report to a Slack incoming webhook
  (or set the SLACK_WEBHOOK_URL environment variable)

Only the standard library is used.
"""
import argparse
import json
import os
import random
import re
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from html import escape
from html.parser import HTMLParser

DEMO_SEED = 21                         # fixed seed so the sample output is the same every time
THRESHOLD = 90.0                       # the standard: a group below this percentage gets flagged
DEVICES = ["Video", "DMXX", "Scanner", "Keypad", "PSlip Printer"]

# chance that a given device is down at a given moment (DMXX scanners and video are the usual culprits)
FAIL_RATE = {"Video": 0.020, "DMXX": 0.045, "Scanner": 0.012, "Keypad": 0.006, "PSlip Printer": 0.010}

# number of stations in each section of each floor, plus how stations are named
FLOORS = {
    "Floor 1": {"Bag": 7, "Induct": 7, "Pack": 24, "Problem Solve": 8},
    "Floor 2": {"Bag": 10, "Induct": 6, "Pack": 10, "Problem Solve": 8},
}


def station_ids(floor, section, count):
    f = floor.split()[-1]
    if section == "Pack":
        return [f"wsF{f}_{101 + i // 2}_{i % 2 + 1:02d}" for i in range(count)]
    if section == "Bag":
        return [f"wsF{f}_{101 + i}_03" for i in range(count)]
    if section == "Induct":
        return [f"F{f}in{101 + i}" for i in range(count)]
    return [f"F{f}ps{101 + i}" for i in range(count)]


# ----------------------------------------------------------------------------------------------------------------------
# 1) FETCH  (synthetic dashboard)
# ----------------------------------------------------------------------------------------------------------------------
def make_snapshot(seed):
    """A fake moment in time: {station_id: {"floor":..., "section":..., "status":..., "devices": {device: ok?}}}."""
    rng = random.Random(seed)
    stations = {}
    for floor, sections in FLOORS.items():
        for section, count in sections.items():
            # some sections are having a bad hour: a shared problem (cabling, a bad update) raises every failure rate
            storm = 3.0 if rng.random() < 0.15 else 1.0
            for sid in station_ids(floor, section, count):
                devices = {d: rng.random() >= min(FAIL_RATE[d] * storm, 0.9) for d in DEVICES}
                stations[sid] = {"floor": floor, "section": section,
                                 "status": rng.choice(["Active", "Active", "Sleeping"]), "devices": devices}
    return stations


def render_dashboard_html(stations, when):
    """Render the snapshot as an HTML table, the way a dashboard page would present it."""
    rows = []
    for sid, s in stations.items():
        cells = "".join(
            f'<td class="{"ok" if ok else "bad"}" data-device="{escape(d)}">{"●" if ok else "✖"}</td>'
            for d, ok in s["devices"].items())
        rows.append(f'<tr data-floor="{s["floor"]}" data-section="{s["section"]}"><td class="station">{sid}</td>'
                    f'<td class="status">{s["status"]}</td><td>{when:%Y-%m-%d %H:%M:%S}</td>{cells}</tr>')
    header = "".join(f"<th>{h}</th>" for h in ["Station", "Status", "Check In"] + DEVICES)
    return f"<html><body><table id='stations'><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"


def fetch_dashboard_html(seed, when):
    """In production this would be an authenticated HTTP request to the dashboard. Here it generates the page."""
    return render_dashboard_html(make_snapshot(seed), when)


# ----------------------------------------------------------------------------------------------------------------------
# 2) PARSE
# ----------------------------------------------------------------------------------------------------------------------
class DashboardParser(HTMLParser):
    """Reads the station table into a list of {"station", "floor", "section", "status", "defects": [device, ...]}."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr" and "data-floor" in a:
            self._row = {"floor": a["data-floor"], "section": a["data-section"], "station": "", "status": "", "defects": []}
        elif tag == "td" and self._row is not None:
            self._cell = a

    def handle_data(self, data):
        if self._row is None or self._cell is None:
            return
        cls = self._cell.get("class")
        if cls == "station":
            self._row["station"] += data.strip()
        elif cls == "status":
            self._row["status"] += data.strip()
        elif cls == "bad":
            self._row["defects"].append(self._cell.get("data-device", "Unknown"))

    def handle_endtag(self, tag):
        if tag == "td":
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def parse_dashboard(html):
    p = DashboardParser()
    p.feed(html)
    return p.rows


# ----------------------------------------------------------------------------------------------------------------------
# 3) EVALUATE
# ----------------------------------------------------------------------------------------------------------------------
def evaluate(rows):
    """{(floor, section): {"pct": health %, "total": n, "problems": [(station, [devices]), ...]}}"""
    groups = defaultdict(lambda: {"total": 0, "bad": 0, "problems": []})
    for r in rows:
        g = groups[(r["floor"], r["section"])]
        g["total"] += 1
        if r["defects"]:
            g["bad"] += 1
            g["problems"].append((r["station"], r["defects"]))
    out = {}
    for key, g in groups.items():
        out[key] = {"pct": 100.0 * (g["total"] - g["bad"]) / g["total"], "total": g["total"],
                    "problems": sorted(g["problems"])}
    return out


# ----------------------------------------------------------------------------------------------------------------------
# 4) REPORT
# ----------------------------------------------------------------------------------------------------------------------
def build_report(results, when, threshold=THRESHOLD):
    lines = [f"📊 *Station Health Update* — {when:%m/%d/%Y %H:%M}", ""]
    below = []
    for floor in FLOORS:
        lines.append(f"*{floor}*")
        for section in FLOORS[floor]:
            r = results[(floor, section)]
            if r["pct"] >= threshold:
                lines.append(f"✅ {floor} {section}: {r['pct']:.1f}%")
            else:
                lines.append(f"🚨 {floor} {section}: {r['pct']:.1f}% ⚠️ BELOW {threshold:.0f}%")
                below.append((floor, section, r))
        lines.append("")
    if not below:
        lines.append("✅ All sections are at or above standard.")
        return "\n".join(lines)
    lines.append(f"🚨 *STATIONS WITH ISSUES (sections below {threshold:.0f}%):*")
    for floor, section, r in below:
        lines.append(f"\n*{floor} {section} ({r['pct']:.1f}%):*")
        for station, defects in r["problems"]:
            lines.append(f"  • `{station}` — {', '.join(defects)}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------------
# 5) DELIVER
# ----------------------------------------------------------------------------------------------------------------------
def post_to_slack(webhook_url, text):
    req = urllib.request.Request(webhook_url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def run_once(seed, when, webhook=None):
    html = fetch_dashboard_html(seed, when)
    results = evaluate(parse_dashboard(html))
    text = build_report(results, when)
    print(text)
    if webhook:
        print(f"\n[posted to Slack: HTTP {post_to_slack(webhook, text)}]")
    return results, text


# ----------------------------------------------------------------------------------------------------------------------
# Savings model (how the business impact was measured)
# ----------------------------------------------------------------------------------------------------------------------
def annual_savings(seconds_saved_per_issue, cost_per_second_per_issue, months_measured=1):
    """Dollar value of the downtime the bot removed.

    For each issue type (DMXX, video, scanner, keypad, printer):  seconds of downtime saved  x  cost of that issue per
    second.  Sum over issue types to get the savings over the measured period, then scale to a year:
        annual = sum(seconds_saved[i] * cost_per_second[i]) * 12 / months_measured
    """
    measured = sum(seconds_saved_per_issue[i] * cost_per_second_per_issue[i] for i in seconds_saved_per_issue)
    return measured * 12 / months_measured


# ----------------------------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="SLIM station-health Slack bot (synthetic-data demo)")
    ap.add_argument("--demo", action="store_true", help="print three deterministic hourly sample reports")
    ap.add_argument("--once", action="store_true", help="run one report now")
    ap.add_argument("--loop", action="store_true", help="run a report every --interval seconds (default 3600)")
    ap.add_argument("--interval", type=int, default=3600)
    ap.add_argument("--webhook", default=os.environ.get("SLACK_WEBHOOK_URL"), help="Slack incoming-webhook URL")
    args = ap.parse_args()

    if args.demo:
        start = datetime(2026, 6, 24, 7, 0)
        for i in range(3):
            when = start + timedelta(hours=i)
            print("=" * 64)
            run_once(seed=DEMO_SEED + i, when=when)
        print("=" * 64)
        return
    if args.loop:
        while True:
            now = datetime.now()
            run_once(seed=int(now.timestamp()), when=now, webhook=args.webhook)
            time.sleep(args.interval)
    run_once(seed=int(time.time()), when=datetime.now(), webhook=args.webhook)


if __name__ == "__main__":
    main()
