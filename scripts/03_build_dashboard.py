"""
03_build_dashboard.py
Week 10: IAM Metrics Dashboard

Reads the raw files written by 02_collect_identity_data.py, compares them to
the HR roster, scores six KPIs, and writes:

  output/iam_metrics_dashboard.html   the dashboard leadership looks at
  output/kpi_history.csv              one line per run, so progress is visible
  evidence/kpi_summary_<time>.csv     the six scores for this run
  evidence/findings_<time>.csv        every account behind every score

Plain English: script 02 collected the attendance sheets. This script is the
teacher grading them against the official enrollment list, then pinning the
report card to the wall.

This script makes no API calls. Every number can be traced to a CSV row.
"""

import csv
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
EVIDENCE_DIR = PROJECT_ROOT / "evidence"
load_dotenv(PROJECT_ROOT / ".env")

# Thresholds. Lab accounts are only days old, so the dormant threshold is
# shorter than the 90 days a production tenant would use.
DORMANT_DAYS = int(os.getenv("DORMANT_DAYS", "14"))
PROVISIONING_SLA_HOURS = int(os.getenv("PROVISIONING_SLA_HOURS", "48"))
DEPROVISIONING_SLA_HOURS = int(os.getenv("DEPROVISIONING_SLA_HOURS", "24"))
MFA_TARGET_PCT = 95
SENSITIVE_GROUP_PREFIXES = ("CA_Exclude",)

DISABLED_STATES = {"DISABLED", "SUSPENDED", "DEPROVISIONED"}
NOT_ACTIVATED_STATES = {"PROVISIONED", "STAGED"}
PLATFORM_NAMES = {"entra": "Entra ID", "okta": "Okta"}



def read_collection_time():
    """Only grade data from a collection that finished. Returns when it was pulled."""
    marker_path = OUTPUT_DIR / "last_collection.json"
    if not marker_path.exists():
        sys.exit("No collection marker found. Run scripts\\02_collect_identity_data.py first.")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("status") != "ok":
        sys.exit("The last data collection failed, so the CSV files are stale.\n"
                 "Fix the collector error and rerun scripts\\02_collect_identity_data.py "
                 "before building the dashboard.")
    return datetime.fromisoformat(marker["collected_at"])


# Every score is measured as of the moment the data was pulled, not the moment
# this script happens to run.
NOW = read_collection_time()


# ------------------------------------------------------------------ Helpers

def load_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def parse_time(text):
    """Turn an API timestamp or a plain date into a UTC datetime. Blank gives None."""
    if not text:
        return None
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def hours_between(start, end):
    return max((end - start).total_seconds() / 3600, 0)


def finding(account, detail):
    return {
        "platform": PLATFORM_NAMES[account["platform"]],
        "login": account["login"],
        "name": account["display_name"],
        "detail": detail,
    }


# --------------------------------------------------------------- Load data

def load_accounts():
    """Join every account to the HR roster and the exceptions register."""
    roster = load_csv(INPUT_DIR / "hr_roster.csv")
    exceptions = load_csv(INPUT_DIR / "account_exceptions.csv")

    employee_by_login = {}
    for person in roster:
        for platform in ("entra", "okta"):
            login = person.get(f"{platform}_login", "").strip().lower()
            if login:
                employee_by_login[(platform, login)] = person
    exception_by_login = {(e["platform"], e["login"].strip().lower()): e for e in exceptions}

    accounts = load_csv(OUTPUT_DIR / "entra_users.csv") + load_csv(OUTPUT_DIR / "okta_users.csv")
    for a in accounts:
        key = (a["platform"], a["login"].strip().lower())
        a["enabled"] = a["status"] not in DISABLED_STATES
        a["employee"] = employee_by_login.get(key)
        a["exception"] = exception_by_login.get(key)
        a["mfa"] = a["mfa_registered"] == "True"
    return accounts, roster, exceptions


# -------------------------------------------------------------------- KPIs

def kpi_orphaned(accounts):
    enabled = [a for a in accounts if a["enabled"]]
    orphans = [a for a in enabled if not a["employee"] and not a["exception"]]
    naive = [a for a in enabled if not a["manager"]]
    return {
        "key": "orphaned_accounts",
        "title": "Orphaned accounts",
        "meaning": "Enabled accounts that match nobody on the HR roster and are not on the exceptions register.",
        "value": str(len(orphans)),
        "unit": f"of {len(enabled)} enabled accounts",
        "target": "0",
        "verdict": "good" if not orphans else "bad",
        "number": len(orphans),
        "note": f"A simpler rule (no manager on file) would have flagged {len(naive)} accounts. "
                f"Matching against HR and the exceptions register leaves {len(orphans)}.",
        "findings": [finding(a, "No HR record and no documented exception") for a in orphans],
    }


def kpi_mfa(accounts):
    enabled = [a for a in accounts if a["enabled"]]
    registered = [a for a in enabled if a["mfa"]]
    pct = round(100 * len(registered) / len(enabled)) if enabled else 0
    parts = []
    for platform, label in PLATFORM_NAMES.items():
        group = [a for a in enabled if a["platform"] == platform]
        parts.append(f"{label} {sum(a['mfa'] for a in group)} of {len(group)}")
    return {
        "key": "mfa_adoption_rate",
        "title": "MFA adoption rate",
        "meaning": "Share of enabled accounts with at least one MFA method registered.",
        "value": f"{pct}%",
        "unit": f"{len(registered)} of {len(enabled)} accounts",
        "target": f"{MFA_TARGET_PCT}% or higher",
        "verdict": "good" if pct >= MFA_TARGET_PCT else "warn" if pct >= 80 else "bad",
        "number": pct,
        "note": ", ".join(parts) + ".",
        "findings": [finding(a, "No MFA method registered") for a in enabled if not a["mfa"]],
    }


def kpi_provisioning(accounts, roster):
    measured, late, never_activated = [], [], []
    for a in accounts:
        person = a["employee"]
        if not person:
            continue
        hours = hours_between(parse_time(person["hire_date"]), parse_time(a["created"]))
        measured.append(hours)
        if hours > PROVISIONING_SLA_HOURS:
            late.append(finding(a, f"Account created {hours:.0f} hours after hire date {person['hire_date']}"))
        if a["status"] in NOT_ACTIVATED_STATES:
            never_activated.append(a)

    logins_with_account = {(a["platform"], a["login"].lower()) for a in accounts}
    for person in roster:
        if person["job_status"] != "active":
            continue
        for platform in ("entra", "okta"):
            login = person.get(f"{platform}_login", "").strip()
            if login and (platform, login.lower()) not in logins_with_account:
                late.append({"platform": PLATFORM_NAMES[platform], "login": login,
                             "name": person["full_name"], "detail": "On the HR roster but no account exists yet"})

    on_time = sum(1 for h in measured if h <= PROVISIONING_SLA_HOURS)
    pct = round(100 * on_time / len(measured)) if measured else 0
    average = sum(measured) / len(measured) if measured else 0
    return {
        "key": "provisioning_sla",
        "title": "Provisioning SLA",
        "meaning": f"New hires whose account existed within {PROVISIONING_SLA_HOURS} hours of their hire date.",
        "value": f"{pct}%",
        "unit": f"{on_time} of {len(measured)} on time, average {average:.0f} hours",
        "target": "90% or higher",
        "verdict": "good" if pct >= 90 else "warn" if pct >= 70 else "bad",
        "number": pct,
        "note": f"Accounts created but never activated by the user: {len(never_activated)}."
                if never_activated else "Every provisioned account has been activated.",
        "findings": late + [finding(a, "Created but never activated by the user") for a in never_activated],
    }


def kpi_dormant(accounts):
    # Emergency accounts are supposed to sit unused, so they are not measured here.
    measured = [a for a in accounts if a["enabled"]
                and not (a["exception"] and a["exception"]["account_type"] == "emergency_access")]
    dormant = []
    for a in measured:
        last = parse_time(a["last_sign_in"])
        reference = last or parse_time(a["created"])
        days = (NOW - reference).days
        if days >= DORMANT_DAYS:
            detail = (f"Last sign in {days} days ago" if last
                      else f"Never signed in, account is {days} days old")
            dormant.append(finding(a, detail))
    share = len(dormant) / len(measured) if measured else 0
    return {
        "key": "dormant_accounts",
        "title": "Dormant accounts",
        "meaning": f"Enabled accounts with no sign in for {DORMANT_DAYS} days or more.",
        "value": str(len(dormant)),
        "unit": f"of {len(measured)} measured accounts",
        "target": "0",
        "verdict": "good" if not dormant else "warn" if share <= 0.10 else "bad",
        "number": len(dormant),
        "note": f"Lab threshold is {DORMANT_DAYS} days because the tenants are new. Production standard is 90 days.",
        "findings": dormant,
    }


def kpi_over_provisioned(accounts):
    measured = [a for a in accounts if a["enabled"] and not a["exception"]]
    flagged = []
    for a in measured:
        reasons = []
        if a["admin_roles"]:
            reasons.append(f"Holds admin role: {a['admin_roles']}")
        sensitive = [g.strip() for g in a["groups"].split(";")
                     if g.strip().startswith(SENSITIVE_GROUP_PREFIXES)]
        if sensitive:
            reasons.append(f"Member of policy exclusion group: {', '.join(sensitive)}")
        if reasons:
            flagged.append(finding(a, ". ".join(reasons)))
    return {
        "key": "over_provisioned_identities",
        "title": "Over provisioned identities",
        "meaning": "Standard accounts that hold an admin role or sit in a group that exempts them from a security policy.",
        "value": str(len(flagged)),
        "unit": f"of {len(measured)} standard accounts",
        "target": "0",
        "verdict": "good" if not flagged else "bad",
        "number": len(flagged),
        "note": "Admin and emergency accounts on the exceptions register are expected to hold roles and are not counted.",
        "findings": flagged,
    }


def kpi_deprovisioning(accounts):
    open_items, closed_hours, findings = [], [], []
    for a in accounts:
        person = a["employee"]
        if not person or person["job_status"] != "terminated":
            continue
        left = parse_time(person["termination_date"])
        if a["enabled"]:
            overdue = hours_between(left, NOW)
            open_items.append(overdue)
            findings.append(finding(a, f"Left on {person['termination_date']}, account still enabled "
                                       f"{overdue / 24:.1f} days later"))
        else:
            closed = parse_time(a.get("status_changed", ""))
            hours = hours_between(left, closed) if closed else None
            if hours is None:
                findings.append(finding(a, "Disabled, but the disable time was not found in the audit log"))
                continue
            closed_hours.append(hours)
            if hours > DEPROVISIONING_SLA_HOURS:
                findings.append(finding(a, f"Left on {person['termination_date']}, disabled {hours / 24:.1f} "
                                           f"days later, outside the {DEPROVISIONING_SLA_HOURS} hour target"))

    total = len(open_items) + len(closed_hours)
    on_time = sum(1 for h in closed_hours if h <= DEPROVISIONING_SLA_HOURS)
    everything = open_items + closed_hours
    mean_days = sum(everything) / len(everything) / 24 if everything else 0
    if not total:
        value, unit, verdict = "n/a", "no leavers on the roster", "good"
    else:
        value = f"{on_time} of {total}"
        unit = f"leavers closed in {DEPROVISIONING_SLA_HOURS} hours, mean {mean_days:.1f} days"
        verdict = "bad" if open_items else "good" if on_time == total else "warn"
    return {
        "key": "deprovisioning_sla",
        "title": "Deprovisioning SLA",
        "meaning": f"Leavers whose account was disabled within {DEPROVISIONING_SLA_HOURS} hours of their termination date.",
        "value": value,
        "unit": unit,
        "target": "All leavers on time",
        "verdict": verdict,
        "number": len(open_items),
        "note": f"Leaver accounts still enabled right now: {len(open_items)}." if open_items
                else "No leaver account is currently enabled.",
        "findings": findings,
    }


# ------------------------------------------------------------------ Output

VERDICT_TEXT = {"good": "Meets target", "warn": "Watch", "bad": "Needs action"}
VERDICT_MARK = {"good": "&#9679;", "warn": "&#9650;", "bad": "&#9632;"}


def write_evidence(kpis, stamp):
    EVIDENCE_DIR.mkdir(exist_ok=True)
    with open(EVIDENCE_DIR / f"kpi_summary_{stamp}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run_time_utc", "kpi", "value", "detail", "target", "verdict"])
        for k in kpis:
            w.writerow([NOW.isoformat(timespec="seconds"), k["title"], k["value"], k["unit"],
                        k["target"], VERDICT_TEXT[k["verdict"]]])
    with open(EVIDENCE_DIR / f"findings_{stamp}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run_time_utc", "kpi", "platform", "login", "name", "detail"])
        for k in kpis:
            for item in k["findings"]:
                w.writerow([NOW.isoformat(timespec="seconds"), k["title"], item["platform"],
                            item["login"], item["name"], item["detail"]])


def update_history(kpis):
    path = OUTPUT_DIR / "kpi_history.csv"
    header = ["run_time_utc"] + [k["key"] for k in kpis] + ["measures_meeting_target"]
    this_run = NOW.strftime("%Y-%m-%d %H:%M")
    # One line per data collection. Grading the same data twice replaces its line.
    rows = [r for r in (load_csv(path) if path.exists() else []) if r["run_time_utc"] != this_run]
    rows.append(dict(zip(header, [this_run] + [k["value"] for k in kpis]
                         + [sum(k["verdict"] == "good" for k in kpis)])))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    return load_csv(path)


def build_html(kpis, accounts, exceptions, history):
    e = html.escape
    passing = sum(k["verdict"] == "good" for k in kpis)
    counts = {p: sum(a["platform"] == p for a in accounts) for p in PLATFORM_NAMES}

    rows = []
    for k in kpis:
        if k["findings"]:
            items = "".join(
                f"<tr><td>{e(i['platform'])}</td><td>{e(i['name'])}</td>"
                f"<td class='login'>{e(i['login'])}</td><td>{e(i['detail'])}</td></tr>"
                for i in k["findings"])
            detail = (f"<details {'open' if k['verdict'] != 'good' else ''}>"
                      f"<summary>{len(k['findings'])} {'account' if len(k['findings']) == 1 else 'accounts'} behind this number</summary>"
                      f"<table class='findings'><thead><tr><th>Platform</th><th>Name</th><th>Login</th>"
                      f"<th>Why it is listed</th></tr></thead><tbody>{items}</tbody></table></details>")
        else:
            detail = "<p class='clear'>Nothing to fix here.</p>"
        rows.append(f"""
      <section class="measure {k['verdict']}">
        <div class="what">
          <h2>{e(k['title'])}</h2>
          <p>{e(k['meaning'])}</p>
        </div>
        <div class="score">
          <span class="value">{e(k['value'])}</span>
          <span class="unit">{e(k['unit'])}</span>
        </div>
        <div class="target">
          <span class="verdict"><span class="mark">{VERDICT_MARK[k['verdict']]}</span> {VERDICT_TEXT[k['verdict']]}</span>
          <span class="unit">Target: {e(k['target'])}</span>
        </div>
        <div class="more">
          <p class="note">{e(k['note'])}</p>
          {detail}
        </div>
      </section>""")

    exception_rows = "".join(
        f"<tr><td>{e(PLATFORM_NAMES[x['platform']])}</td><td class='login'>{e(x['login'])}</td>"
        f"<td>{e(x['account_type'].replace('_', ' '))}</td><td>{e(x['owner'])}</td><td>{e(x['reason'])}</td></tr>"
        for x in exceptions)

    head = "".join(f"<th>{e(k['title'])}</th>" for k in kpis)
    history_rows = "".join(
        "<tr><td>" + e(h["run_time_utc"]) + " UTC</td>"
        + "".join(f"<td>{e(h[k['key']])}</td>" for k in kpis)
        + f"<td>{e(h['measures_meeting_target'])} of {len(kpis)}</td></tr>"
        for h in history[-10:])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IAM posture report</title>
<style>
  :root {{
    --paper: #f6f7f9; --sheet: #ffffff; --ink: #16202e; --quiet: #5a6577; --rule: #d9dee6;
    --good: #1f7a4d; --warn: #a86200; --bad: #b3261e; --accent: #2f4b7c;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--paper); color: var(--ink);
         font: 16px/1.5 "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif; }}
  main {{ max-width: 1180px; margin: 0 auto; padding: 40px 28px 64px; }}
  h1, h2, h3, .value, .tally {{ font-family: Bahnschrift, "DIN Alternate", "Segoe UI", sans-serif; }}
  header {{ display: grid; grid-template-columns: 1fr auto; gap: 24px; align-items: end;
           border-bottom: 3px solid var(--ink); padding-bottom: 20px; }}
  h1 {{ font-size: 40px; line-height: 1.1; margin: 0 0 6px; font-weight: 600; }}
  header p {{ margin: 0; color: var(--quiet); max-width: 62ch; }}
  .tally {{ text-align: right; font-size: 56px; line-height: 1; font-weight: 600; }}
  .tally small {{ display: block; font: 14px/1.4 "Segoe UI", sans-serif; color: var(--quiet); margin-top: 6px; }}
  .measure {{ display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 3fr) minmax(0, 2fr);
             gap: 8px 28px; align-items: start; padding: 22px 0 22px 18px;
             border-bottom: 1px solid var(--rule); border-left: 6px solid var(--rule); margin-top: 18px;
             background: var(--sheet); padding-right: 22px; }}
  .measure.good {{ border-left-color: var(--good); }}
  .measure.warn {{ border-left-color: var(--warn); }}
  .measure.bad {{ border-left-color: var(--bad); }}
  .measure h2 {{ margin: 0 0 4px; font-size: 22px; font-weight: 600; }}
  .measure p {{ margin: 0; color: var(--quiet); }}
  .value {{ display: block; font-size: 44px; line-height: 1; font-weight: 600; }}
  .unit {{ display: block; color: var(--quiet); font-size: 14px; margin-top: 6px; }}
  .verdict {{ display: block; font-weight: 600; }}
  .good .verdict {{ color: var(--good); }} .warn .verdict {{ color: var(--warn); }} .bad .verdict {{ color: var(--bad); }}
  .more {{ grid-column: 1 / -1; margin-top: 8px; }}
  .note {{ font-size: 14px; }}
  .clear {{ font-size: 14px; color: var(--good) !important; margin-top: 6px !important; }}
  details {{ margin-top: 10px; }}
  summary {{ cursor: pointer; color: var(--accent); font-weight: 600; font-size: 14px; }}
  summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 3px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 10px; }}
  th {{ text-align: left; font-weight: 600; color: var(--quiet); border-bottom: 1px solid var(--ink); padding: 6px 12px 6px 0; }}
  td {{ border-bottom: 1px solid var(--rule); padding: 7px 12px 7px 0; vertical-align: top; }}
  .login {{ overflow-wrap: anywhere; }}
  .scroll {{ overflow-x: auto; }}
  h3 {{ font-size: 22px; font-weight: 600; margin: 44px 0 4px; }}
  h3 + p {{ margin: 0; color: var(--quiet); max-width: 75ch; }}
  footer {{ margin-top: 44px; padding-top: 16px; border-top: 3px solid var(--ink); color: var(--quiet); font-size: 14px; }}
  @media (max-width: 760px) {{
    header, .measure {{ grid-template-columns: 1fr; }}
    .tally {{ text-align: left; }}
  }}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>IAM posture report</h1>
      <p>Six identity health measures across Microsoft Entra ID ({counts['entra']} accounts) and
         Okta ({counts['okta']} accounts), checked against the HR roster.
         Data pulled {NOW.strftime('%B %d, %Y at %H:%M')} UTC.</p>
    </div>
    <div class="tally">{passing} of {len(kpis)}<small>measures meet target</small></div>
  </header>
  {''.join(rows)}

  <h3>Exceptions register</h3>
  <p>Accounts that are allowed to exist without an HR record. Each one has a named owner and a written reason.</p>
  <div class="scroll"><table><thead><tr><th>Platform</th><th>Login</th><th>Type</th><th>Owner</th><th>Reason</th></tr></thead>
  <tbody>{exception_rows}</tbody></table></div>

  <h3>Run history</h3>
  <p>One line per data collection, oldest first. This is how progress between runs is shown.</p>
  <div class="scroll"><table><thead><tr><th>Run</th>{head}<th>Meeting target</th></tr></thead>
  <tbody>{history_rows}</tbody></table></div>

  <footer>
    Sources: Microsoft Graph API, Okta API, input/hr_roster.csv (simulated HR data for a lab).
    Thresholds: dormant after {DORMANT_DAYS} days, provisioning within {PROVISIONING_SLA_HOURS} hours,
    deprovisioning within {DEPROVISIONING_SLA_HOURS} hours, MFA target {MFA_TARGET_PCT}%.
    Raw data and findings for this run are saved in the output and evidence folders.
  </footer>
</main>
</body>
</html>
"""


def main():
    accounts, roster, exceptions = load_accounts()
    kpis = [
        kpi_orphaned(accounts),
        kpi_mfa(accounts),
        kpi_provisioning(accounts, roster),
        kpi_dormant(accounts),
        kpi_over_provisioned(accounts),
        kpi_deprovisioning(accounts),
    ]

    stamp = NOW.strftime("%Y%m%d_%H%M")
    write_evidence(kpis, stamp)
    history = update_history(kpis)
    dashboard = OUTPUT_DIR / "iam_metrics_dashboard.html"
    dashboard.write_text(build_html(kpis, accounts, exceptions, history), encoding="utf-8")

    print("=" * 104)
    print(f"Week 10 IAM metrics   data pulled {NOW.strftime('%Y-%m-%d %H:%M')} UTC   {len(accounts)} accounts scored")
    print("=" * 104)
    print(f"{'KPI':<30} {'value':<10} {'detail':<48} verdict")
    print("-" * 104)
    for k in kpis:
        print(f"{k['title']:<30} {k['value']:<10} {k['unit'][:47]:<48} {VERDICT_TEXT[k['verdict']]}")
    print("-" * 104)
    print(f"{sum(k['verdict'] == 'good' for k in kpis)} of {len(kpis)} measures meet target")
    print(f"\nOrphan rule check: {kpis[0]['note']}")
    print(f"\nDashboard: {dashboard.relative_to(PROJECT_ROOT)}")
    print(f"Evidence:  evidence\\kpi_summary_{stamp}.csv and evidence\\findings_{stamp}.csv")


if __name__ == "__main__":
    main()
