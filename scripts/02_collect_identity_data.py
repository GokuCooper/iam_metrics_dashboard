"""
02_collect_identity_data.py
Week 10: IAM Metrics Dashboard

Pulls the raw facts about every identity from Microsoft Entra ID and Okta
and saves them as two CSV files in the output folder.

Plain English: before we can grade anything, we need the class roster.
This script asks each platform for its roster plus a few facts per person:
who their manager is, whether they set up MFA, how many groups they are in,
whether they hold admin roles, and when they last signed in.

No KPI math happens here. Collect first, calculate second. That way the
numbers on the dashboard can always be traced back to a raw file.
"""

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
load_dotenv(PROJECT_ROOT / ".env")

GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT = 30


# ---------------------------------------------------------------- Entra ID

def get_graph_token():
    """Trade the client ID and secret for a short lived Graph access token."""
    url = f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}/oauth2/v2.0/token"
    body = {
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET"),
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    resp = requests.post(url, data=body, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["access_token"]


def graph_get_all(token, path):
    """Return every item from a Graph list call, following next page links."""
    headers = {"Authorization": f"Bearer {token}"}
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    items = []
    while url:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return items


def find_disable_times(token, disabled_ids):
    """Look through the user management audit log for the latest disable event per user."""
    try:
        audits = graph_get_all(token, "/auditLogs/directoryAudits?$filter=category eq 'UserManagement'")
    except requests.HTTPError:
        print("Note: could not read the audit log, disable times will be blank.")
        return {}
    found = {}
    for event in audits:
        for target in event.get("targetResources") or []:
            if target.get("id") not in disabled_ids:
                continue
            turned_off = any(
                (p.get("displayName") == "AccountEnabled" and "false" in (p.get("newValue") or "").lower())
                for p in target.get("modifiedProperties") or []
            )
            if event.get("activityDisplayName") == "Disable account" or turned_off:
                when = event.get("activityDateTime", "")
                if when > found.get(target["id"], ""):
                    found[target["id"]] = when
    return found


def collect_entra():
    token = get_graph_token()

    # 1. The roster, including the last sign in stamp for each account
    users = graph_get_all(
        token,
        "/users?$select=id,displayName,userPrincipalName,accountEnabled,"
        "createdDateTime,userType,department,employeeId,signInActivity&$top=100",
    )

    # 2. Who reports to whom. An account with no manager has no owner.
    with_manager = graph_get_all(
        token, "/users?$select=id&$expand=manager($select=userPrincipalName)&$top=100"
    )
    manager_by_id = {
        u["id"]: (u.get("manager") or {}).get("userPrincipalName", "") for u in with_manager
    }

    # 3. MFA registration, one row per user
    mfa_rows = graph_get_all(token, "/reports/authenticationMethods/userRegistrationDetails")
    mfa_by_id = {m["id"]: m for m in mfa_rows}

    # 4. Admin role assignments (active assignments, not PIM eligible ones)
    assignments = graph_get_all(
        token, "/roleManagement/directory/roleAssignments?$expand=roleDefinition($select=displayName)"
    )
    roles_by_id = {}
    for a in assignments:
        role_name = (a.get("roleDefinition") or {}).get("displayName", "unknown role")
        roles_by_id.setdefault(a["principalId"], []).append(role_name)

    # 5. For disabled accounts, find out when they were disabled.
    #    Entra does not store that on the user, so we read it from the audit log.
    disabled_ids = {u["id"] for u in users if not u.get("accountEnabled")}
    disabled_at = find_disable_times(token, disabled_ids) if disabled_ids else {}

    rows = []
    for u in users:
        uid = u["id"]

        # 6. Group memberships for this one user
        member_of = graph_get_all(token, f"/users/{uid}/memberOf?$select=id,displayName")
        groups = [m.get("displayName", "") for m in member_of
                  if m.get("@odata.type") == "#microsoft.graph.group"]

        sign_in = u.get("signInActivity") or {}
        last_sign_in = sign_in.get("lastSuccessfulSignInDateTime") or sign_in.get("lastSignInDateTime") or ""
        mfa = mfa_by_id.get(uid, {})
        roles = sorted(roles_by_id.get(uid, []))

        rows.append({
            "platform": "entra",
            "user_id": uid,
            "login": u.get("userPrincipalName", ""),
            "display_name": u.get("displayName", ""),
            "status": "ACTIVE" if u.get("accountEnabled") else "DISABLED",
            "user_type": u.get("userType") or "",
            "department": u.get("department") or "",
            "employee_id": u.get("employeeId") or "",
            "manager": manager_by_id.get(uid, ""),
            "created": u.get("createdDateTime") or "",
            "activated": u.get("createdDateTime") or "",
            "status_changed": disabled_at.get(uid, ""),
            "last_sign_in": last_sign_in,
            "mfa_registered": bool(mfa.get("isMfaRegistered", False)),
            "mfa_methods": "; ".join(mfa.get("methodsRegistered", [])),
            "group_count": len(groups),
            "groups": "; ".join(sorted(groups)),
            "admin_role_count": len(roles),
            "admin_roles": "; ".join(roles),
        })
    return rows


# -------------------------------------------------------------------- Okta

def okta_get_all(path):
    """Return every item from an Okta list call, following the Link header."""
    base = os.getenv("OKTA_ORG_URL").rstrip("/")
    headers = {"Authorization": f"SSWS {os.getenv('OKTA_API_TOKEN')}", "Accept": "application/json"}
    url = path if path.startswith("http") else f"{base}{path}"
    items = []
    while url:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        items.extend(resp.json())
        url = resp.links.get("next", {}).get("url")
    return items


def okta_get_optional(path):
    """Same as okta_get_all, but a 404 means there is nothing to list, not a failure."""
    try:
        return okta_get_all(path)
    except requests.HTTPError as exc:
        if exc.response.status_code == 404:
            return []
        raise


def collect_okta():
    # The normal list hides deactivated users, so ask for those separately.
    # Leavers matter for the deprovisioning KPI.
    users = okta_get_all("/api/v1/users?limit=200")
    users += okta_get_all('/api/v1/users?limit=200&filter=status eq "DEPROVISIONED"')

    rows = []
    for u in users:
        uid = u["id"]
        profile = u.get("profile", {})

        # Okta wipes factors, groups, and roles when a user is deactivated, and
        # answers 404 if you ask for them. For a leaver, empty is the right answer.
        factors = okta_get_optional(f"/api/v1/users/{uid}/factors")
        active_factors = sorted({f.get("factorType", "") for f in factors if f.get("status") == "ACTIVE"})

        # Everyone is a built in group that every user gets, so it is not counted
        groups = [g["profile"]["name"] for g in okta_get_optional(f"/api/v1/users/{uid}/groups")
                  if g["profile"]["name"] != "Everyone"]

        roles = sorted(r.get("label", r.get("type", "")) for r in okta_get_optional(f"/api/v1/users/{uid}/roles"))

        rows.append({
            "platform": "okta",
            "user_id": uid,
            "login": profile.get("login", ""),
            "display_name": f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip(),
            "status": u.get("status", ""),
            "user_type": profile.get("userType") or "",
            "department": profile.get("department") or "",
            "employee_id": profile.get("employeeNumber") or "",
            "manager": profile.get("manager") or "",
            "created": u.get("created") or "",
            "activated": u.get("activated") or "",
            "status_changed": u.get("statusChanged") or "",
            "last_sign_in": u.get("lastLogin") or "",
            "mfa_registered": len(active_factors) > 0,
            "mfa_methods": "; ".join(active_factors),
            "group_count": len(groups),
            "groups": "; ".join(sorted(groups)),
            "admin_role_count": len(roles),
            "admin_roles": "; ".join(roles),
        })
    return rows


# ------------------------------------------------------------------ Output

def write_marker(status, when):
    """Leave a note saying whether this collection finished. The dashboard reads it
    and refuses to grade data from a run that failed part way through."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    marker = {"status": status, "collected_at": when.isoformat(timespec="seconds")}
    (OUTPUT_DIR / "last_collection.json").write_text(json.dumps(marker), encoding="utf-8")


def write_csv(rows, filename):
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def print_table(title, rows):
    print(f"\n{title}")
    print(f"{'login':<52} {'status':<14} {'mgr':<4} {'mfa':<4} {'grp':<4} {'roles':<6} last sign in")
    print("-" * 106)
    for r in rows:
        print(
            f"{r['login'][:51]:<52} {r['status']:<14} "
            f"{'yes' if r['manager'] else 'no':<4} {'yes' if r['mfa_registered'] else 'no':<4} "
            f"{r['group_count']:<4} {r['admin_role_count']:<6} {(r['last_sign_in'] or 'never')[:10]}"
        )


def main():
    now = datetime.now(timezone.utc)
    started = now.strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 104)
    print(f"Week 10 identity data collection   run at {started}")
    print("=" * 104)
    write_marker("failed", now)

    try:
        entra = collect_entra()
        okta = collect_okta()
    except requests.HTTPError as exc:
        print(f"\nAPI call failed: HTTP {exc.response.status_code} on {exc.request.url.split('?')[0]}")
        print(exc.response.text[:300])
        print("\nCollection did not finish. The CSV files in output are from an earlier run.")
        sys.exit(1)

    print_table("Microsoft Entra ID", entra)
    print_table("Okta", okta)

    entra_path = write_csv(entra, "entra_users.csv")
    okta_path = write_csv(okta, "okta_users.csv")
    print(f"\nSaved {len(entra)} Entra rows to {entra_path.relative_to(PROJECT_ROOT)}")
    print(f"Saved {len(okta)} Okta rows to {okta_path.relative_to(PROJECT_ROOT)}")
    write_marker("ok", now)


if __name__ == "__main__":
    main()
