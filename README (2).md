# SLIM Station-Health Slack Bot

An hourly Slack bot that watches the health of every workstation on a warehouse floor, flags any group of stations that
falls below the 90% standard, and tells the floor team **exactly which stations are broken and what is wrong with each
one** so they can go fix it.

> **This repository is a demo rebuild.** The original bot read an internal dashboard. This version generates
> **synthetic data** and parses it the same way, so it runs anywhere, needs no login, and contains no company data.
> Station names, results and sample reports below are simulated.

## The problem
Each station on the floor has connected devices: **video, DMXX scanner, standard scanner, keypad and a packing-slip
printer**. When one fails, the station slows down or stops. The dashboard showed device status station by station, but
nobody was watching it hour to hour, and a broken device could sit unnoticed for a long time.

## What the bot does
1. **Fetches** the dashboard page (the demo generates a synthetic page; the original made authenticated requests).
2. **Parses** the HTML table into one record per station, with a pass/fail flag for each device.
3. **Evaluates** health per group: stations with no device defects / stations in the group.
4. **Reports** every hour: groups at or above 90% get a check mark; groups below it get an alert, followed by the
   stations and defect types behind it.
5. **Delivers** the report to a Slack channel through an incoming webhook (console output in the demo).

## Results
In the original deployment, the bot produced a **6% reduction in station downtime**. I measured that reduction over one
month, then broke it down by issue type. For each issue (DMXX, video, scanner, keypad, printer) I multiplied the
downtime saved by the cost per second of that issue, and added the results together. Projecting the month over a full
year gives about **$180K in annual savings**.

```
annual savings = ( sum over issues of  seconds_saved[issue] x cost_per_second[issue] ) x 12 / months_measured
```
`annual_savings()` in `slim_bot.py` implements this calculation. The $180K is a projection from one measured month, not
a full year of observed data.

## Sample output (simulated data)
```text
📊 *Station Health Update* — 06/24/2026 07:00

*Floor 1*
🚨 Floor 1 Bag: 71.4% ⚠️ BELOW 90%
✅ Floor 1 Induct: 100.0%
✅ Floor 1 Pack: 95.8%
🚨 Floor 1 Problem Solve: 87.5% ⚠️ BELOW 90%

*Floor 2*
✅ Floor 2 Bag: 90.0%
🚨 Floor 2 Induct: 50.0% ⚠️ BELOW 90%
✅ Floor 2 Pack: 100.0%
✅ Floor 2 Problem Solve: 100.0%

🚨 *STATIONS WITH ISSUES (sections below 90%):*

*Floor 1 Bag (71.4%):*
  • `wsF1_106_03` — DMXX
  • `wsF1_107_03` — DMXX

*Floor 1 Problem Solve (87.5%):*
  • `F1ps105` — Scanner

*Floor 2 Induct (50.0%):*
  • `F2in102` — PSlip Printer
  • `F2in104` — Video, DMXX
  • `F2in105` — Video
```
More sample reports are in `sample_output.txt`.

## Run it
Python 3.8+, standard library only.
```bash
python3 slim_bot.py --demo                 # three simulated hourly reports
python3 slim_bot.py --once                 # one report now
python3 slim_bot.py --loop                 # a report every hour
python3 slim_bot.py --once --webhook URL   # also post to Slack (or set SLACK_WEBHOOK_URL)
```

## Tech
Python, HTML parsing (`html.parser`), Slack incoming webhooks (`urllib`), scheduling loop. No third-party packages.

## What I would add next
Trend tracking across hours, alert escalation when a station stays broken for several hours, and per-issue repair
time statistics.
