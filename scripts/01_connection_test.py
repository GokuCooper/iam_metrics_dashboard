"""
01_connection_test.py
Week 10: IAM Metrics Dashboard

Preflight check. Before building any KPI, prove that both identity
platforms answer and that every permission the dashboard needs works.

Plain English: we knock on six doors. Each door either opens (PASS)
or stays shut (FAIL), and we print which one and why.

Secrets are read from the .env file in the project root.
This script never prints a secret value.
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# The .env file lives one folder above /scripts
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

REQUIRED = ["TENANT_ID", "CLIENT_ID", "CLIENT_SECRET", "OKTA_ORG_URL", "OKTA_API_TOKEN"]
GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT = 30

results = []


def record(name, ok, detail):
    """Save one PASS or FAIL line and print it right away."""
    status = "PASS" if ok else "FAIL"
    results.append(ok)
    print(f"[{status}] {name:<42} {detail}")


def check_env():
    """Make sure every setting exists. Only names are printed, never values."""
    missing = [key for key in REQUIRED if not os.getenv(key)]
    if missing:
        print("Missing settings in .env: " + ", ".join(missing))
        sys.exit(1)
    print("All 5 required settings found in .env\n")


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
    if resp.status_code != 200:
        error = resp.json().get("error_description", resp.text).splitlines()[0]
        record("Entra: get access token", False, error)
        return None
    record("Entra: get access token", True, "token issued")
    return resp.json()["access_token"]


def graph_check(token, name, path, count_all=False):
    """Call one Graph endpoint and report what came back."""
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH}{path}"
    total = 0
    try:
        while url:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
            if resp.status_code != 200:
                err = resp.json().get("error", {})
                record(name, False, f"HTTP {resp.status_code} {err.get('code', '')}: {err.get('message', '')[:90]}")
                return
            data = resp.json()
            total += len(data.get("value", []))
            # Follow the next page link only when we want a full count
            url = data.get("@odata.nextLink") if count_all else None
    except requests.RequestException as exc:
        record(name, False, f"network error: {exc}")
        return
    detail = f"{total} users found" if count_all else "permission works"
    record(name, True, detail)


def okta_check():
    """Count Okta users, following the Link header for extra pages."""
    base = os.getenv("OKTA_ORG_URL").rstrip("/")
    headers = {
        "Authorization": f"SSWS {os.getenv('OKTA_API_TOKEN')}",
        "Accept": "application/json",
    }
    url = f"{base}/api/v1/users?limit=200"
    total = 0
    try:
        while url:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT)
            if resp.status_code != 200:
                summary = resp.json().get("errorSummary", resp.text[:90])
                record("Okta: list users", False, f"HTTP {resp.status_code}: {summary}")
                return
            total += len(resp.json())
            url = resp.links.get("next", {}).get("url")
    except requests.RequestException as exc:
        record("Okta: list users", False, f"network error: {exc}")
        return
    record("Okta: list users", True, f"{total} users found")


def main():
    print("=" * 78)
    print("Week 10 connection test: Microsoft Entra ID + Okta")
    print("=" * 78)
    check_env()

    token = get_graph_token()
    if token:
        graph_check(token, "Entra: list users (User.Read.All)",
                    "/users?$select=id&$top=999", count_all=True)
        graph_check(token, "Entra: sign in activity (AuditLog.Read.All)",
                    "/users?$select=id,signInActivity&$top=1")
        graph_check(token, "Entra: MFA registration (AuditLog.Read.All)",
                    "/reports/authenticationMethods/userRegistrationDetails?$top=1")
        graph_check(token, "Entra: group members (GroupMember.Read.All)",
                    "/groups?$select=id&$top=1")
        graph_check(token, "Entra: role assignments (RoleManagement)",
                    "/roleManagement/directory/roleAssignments?$top=1")

    okta_check()

    print("-" * 78)
    passed = sum(results)
    print(f"{passed} of {len(results)} checks passed")
    if passed != len(results):
        print("Fix the FAIL lines above before building the dashboard.")
        sys.exit(1)
    print("Both platforms are answering. Ready to build KPIs.")


if __name__ == "__main__":
    main()
