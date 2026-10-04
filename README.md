# IAM Metrics Dashboard: Six KPIs Across Entra ID and Okta

**Week 10 of my 12 week IAM engineering lab. Project 6.**

I built a dashboard in Python that pulls live identity data from Microsoft Entra ID and Okta, compares it to an HR roster, and scores six identity health measures. Then I used it to find real problems, fixed them, and proved the fix with a second run.

| | |
|---|---|
| **Platforms** | Microsoft Entra ID (P2), Okta (Integrator plan) |
| **APIs** | Microsoft Graph v1.0, Okta Core API |
| **Language** | Python 3 (requests, python-dotenv) |
| **Result** | 0 of 6 measures meeting target at baseline, 2 of 6 after one remediation pass, with a third improved |
| **Cost** | $0 |

![Dashboard after remediation](screenshots/25_dashboard_after_remediation.png)

---

## The short version

Think of a school that hands out hall passes. The principal wants one poster by the office that answers six questions at a glance. That poster is this dashboard.

| KPI | School version | What it measures |
|---|---|---|
| Orphaned accounts | Passes with no student name on them | Enabled accounts that match nobody on the HR roster |
| MFA adoption rate | Kids who carry a pass and a student ID | Enabled accounts with MFA registered |
| Provisioning SLA | How fast a new kid gets a pass | Hours from hire date to account creation |
| Dormant accounts | Passes nobody has used in weeks | No sign in for 14 days or more |
| Over provisioned identities | Kids holding passes they do not need | Standard users with admin roles or policy exemptions |
| Deprovisioning SLA | How fast a pass is taken back when a kid moves away | Hours from termination date to account disabled |

---

## Situation

By Week 9 I could pull one report from one platform. That answers "who is inactive?" but it does not answer what leadership and auditors actually ask: "Are we getting better or worse, and can you prove it?"

I wanted one view across both identity platforms, measured against a source of truth, with evidence files behind every number.

---

## Build

### How it fits together

```
input/hr_roster.csv ----------+
input/account_exceptions.csv -+
                              |
Entra ID --(Graph API)--+     v
                        +--> 02 collect --> output/*.csv --> 03 grade --> dashboard.html
Okta ------(Okta API)---+                                             --> evidence/*.csv
                                                                      --> kpi_history.csv
```

I split the work into three scripts on purpose. Collecting and grading are separate jobs, so every number on the dashboard can be traced back to a row in a raw file.

| Script | Job | Plain English |
|---|---|---|
| `01_connection_test.py` | Proves both platforms answer and every permission works | Knock on every door before moving in |
| `02_collect_identity_data.py` | Pulls users, MFA, groups, roles, and sign in data to CSV | Gather the attendance sheets |
| `03_build_dashboard.py` | Scores the six KPIs and writes the dashboard and evidence | Grade the sheets and pin up the report card |

### Step 1: Set up the project and extend the app registration

![Project folders](screenshots/01_week10_project_folder_structure.png)


I reused my Week 9 app registration and added two read only Graph permissions. Least privilege still applies to scripts, so nothing here can write.

| Before | After |
|---|---|
| ![Before](screenshots/02_entra_app_api_permissions_before.png) | ![After](screenshots/03_entra_app_admin_consent_granted.png) |

| Permission | Why the dashboard needs it |
|---|---|
| `User.Read.All` | The list of accounts |
| `AuditLog.Read.All` | Sign in activity, MFA registration, and when an account was disabled |
| `GroupMember.Read.All` | Group memberships |
| `RoleManagement.Read.Directory` | Admin role assignments |

I kept the same tenant ID and client ID and created a new client secret just for this project. One app can hold several keys, and a separate key means I can revoke Week 10 without breaking Week 9.

![Client secret](screenshots/04_entra_week10_client_secret_created_redacted.png)

### Step 2: Create an Okta API token

![Okta token](screenshots/06_okta_api_token_created.png)

Okta asked where calls with this token are allowed to come from. I chose Any IP because my home address changes. In production I would bind the token to a named network zone and create it from a dedicated read only service account, not a super admin.

![IP restriction options](screenshots/06a_okta_token_ip_restriction_options.png)

### Step 3: Keep secrets out of the code

All credentials live in a `.env` file that is listed in `.gitignore`. The repo only carries `.env.example`.

![Env file](screenshots/05_env_file_configured_secrets_redacted.png)

### Step 4: Prove the plumbing first

Before writing any KPI logic I ran a preflight that tests each permission one at a time. If a KPI breaks later, I already know it is a logic problem and not an access problem.

![Connection test](screenshots/07_connection_test_both_platforms_success.png)

### Step 5: Collect the raw data

![Raw data](screenshots/08_raw_identity_data_collected.png)

### Step 6: Add a source of truth

Raw data tells you who exists. It does not tell you who is supposed to exist. I added two input files:

- **`hr_roster.csv`** is the official enrollment list: who works here, their department, hire date, and whether they have left. The HR data is simulated for this lab.
- **`account_exceptions.csv`** is the list of master keys: break glass and admin accounts that are allowed to exist without an HR record. Each one has a named owner and a written reason.

![HR roster](screenshots/09_hr_roster_source_of_truth.png)

### Step 7: Give the dashboard something to catch

My lab already had real findings: MFA gaps, accounts that never signed in, and Okta users stuck in a pending state. To cover every KPI I planted three more problems.

| Planted problem | How |
|---|---|
| A true orphan | Created `temp.contractor01` with no HR record |
| An over provisioned user | Gave a Sales employee the User Administrator role |
| A leaver nobody disabled | Marked an employee terminated in HR and left her account on |

![Orphan planted](screenshots/10_entra_orphan_account_planted.png)

When I assigned the admin role, my own Week 7 PIM settings pushed back. The tenant refused a permanent assignment, capped it at 15 days, and blocked the Assign button until I wrote a justification. The guardrails I built made it harder to over provision someone, and the reason I typed now sits in the audit log.

![PIM blocking the assignment until a justification is entered](screenshots/11_entra_admin_role_assigned_to_sales_user.png)
![Assignment confirmed](screenshots/11a_user_administrator_active_assignment_confirmed.png)
![Data after planting](screenshots/12_raw_identity_data_after_planting.png)

### Step 8: Baseline run

Zero of six measures met target. That is the "before" picture.

![Baseline terminal](screenshots/13_kpi_baseline_run_terminal.png)
![Baseline dashboard](screenshots/14_dashboard_baseline_zero_of_six.png)
![Baseline findings](screenshots/15_dashboard_baseline_findings_detail.png)

Each run also writes evidence files, so the scores can be handed to an auditor without a screenshot.

![Evidence file](screenshots/16_evidence_kpi_summary_csv.png)

### Step 9: Remediate

| Finding | Fix | Proof |
|---|---|---|
| Orphaned contractor account | Disabled it. I disable before deleting so the account can be claimed if someone needs it | ![17](screenshots/17_remediation_orphan_account_disabled.png) |
| Sales user with admin role | Removed the role assignment | ![18](screenshots/18_remediation_admin_role_removed_from_sales_user.png) |
| Stale travel exemption from MFA policy | Removed her from the exclusion group | ![19](screenshots/19_remediation_travel_exclusion_membership_removed.png) |
| Leaver still enabled 4.8 days later | Disabled the account | ![20](screenshots/20_remediation_leaver_account_disabled.png) |
| New leaver the same day | Deactivated in Okta on the termination date | ![21](screenshots/21_remediation_okta_leaver_deactivated_same_day.png) |

---

## Snag

Five things went wrong or needed a second look. This is the part I talk about in interviews.

### 1. A secret showed up in a screenshot

My first screenshot of the `.env` file showed the client secret in full. I treated it as exposed, rotated it, and switched to solid redaction bars instead of blur, since blurred text can sometimes be recovered.

**Lesson:** a secret that has been seen is a secret that has been leaked. Rotate first, discuss later.

### 2. My first orphan rule cried wolf

My first idea was "an account with no manager is an orphan." That rule flagged 10 of 13 accounts, including my break glass account, both admin accounts, and the department head who has no manager because he is the manager.

| Rule | Accounts flagged |
|---|---|
| No manager on file | 10 |
| No HR match and not on the exceptions register | 1 |

The fix was to correlate accounts against the HR roster, the same idea as account correlation in SailPoint from Week 5, and to keep a documented exceptions register for accounts that are ownerless by design.

**Lesson:** a dashboard full of false positives teaches people to ignore it.

### 3. The collector crashed on a deactivated Okta user

After I deactivated a leaver in Okta, the collector died with HTTP 404.

![Collector 404](screenshots/22_snag_collector_404_on_deactivated_okta_user.png)

**Root cause:** when Okta deactivates a user it wipes their factors, groups, and roles. Asking for them returns "not found." My script treated that as a fatal error.

**Fix:** for those three lookups, a 404 now means "nothing there," which is the correct answer for a leaver.

### 4. The dashboard graded old data and stamped it with a new time

This one worried me more than the crash. The collector failed before saving new files, but the dashboard script ran anyway. It read the files from the earlier run and labeled the report with the current time. The scores did not move even though I had fixed five things.

![Stale data](screenshots/23_snag_dashboard_scored_stale_data.png)

**Root cause:** nothing connected the two scripts. The grader had no way to know the collection had failed.

**Fix:** the collector now writes a small marker file saying whether it finished and when. The dashboard refuses to build if the last collection failed, and it reports the time the data was actually pulled. I also removed the bad run from the history and evidence folders.

**Lesson:** a wrong number with a fresh timestamp is worse than no number, because people act on it.

### 5. Not everything can be fixed in an afternoon

Three measures stayed red, and I left them that way instead of gaming the numbers.

| Still open | Why | What closes it |
|---|---|---|
| Provisioning SLA at 62% | It measures the past. I cannot create an account earlier than I did | Automated joiner workflows, like my Week 6 project |
| MFA adoption at 60% | Four users have never signed in to register | Account owners enrolling, enforced by Conditional Access |
| 2 dormant accounts | Okta users who never activated | Owner follow up, then deactivate if unclaimed |

---

## Result

After fixing the collector I pulled fresh data. Two Entra accounts now show as disabled and the Okta leaver shows as deprovisioned.

![Collector after remediation](screenshots/24a_collector_after_remediation.png)
![After terminal](screenshots/24_kpi_after_remediation_terminal.png)
![Run history](screenshots/26_dashboard_run_history_before_and_after.png)

| KPI | Baseline | After remediation | Verdict |
|---|---|---|---|
| Orphaned accounts | 1 | 0 | Meets target |
| MFA adoption rate | 46% | 60% | Needs action |
| Provisioning SLA | 62% | 62% | Needs action |
| Dormant accounts | 3 | 2 | Needs action |
| Over provisioned identities | 1 | 0 | Meets target |
| Deprovisioning SLA | 0 of 1 | 1 of 2 | Watch |
| **Measures meeting target** | **0 of 6** | **2 of 6** | |

The deprovisioning story in one line: the first leaver sat enabled for 4.8 days until the dashboard caught it. The next leaver was closed the same day.

![Evidence after](screenshots/27_evidence_kpi_summary_after_remediation.png)

As the last step I revoked both credentials used for this lab. The Okta token list is empty, and the only secret left on the Entra app is the one that belongs to Week 9.

| Okta token revoked | Entra Week 10 secret removed |
|---|---|
| ![Okta token revoked](screenshots/28_okta_api_token_revoked.png) | ![Entra secret removed](screenshots/29_entra_week10_client_secret_removed.png) |

---

## Real World Mapping

| What I did in the lab | What it is called on the job |
|---|---|
| Matched accounts to the HR roster | Identity correlation against an authoritative source |
| Kept an exceptions register with owners and reasons | Service and privileged account inventory |
| Wrote findings and summary CSVs each run | Audit evidence for access reviews and SOX style controls |
| Measured hire date to account creation | Joiner SLA |
| Measured termination date to disable | Leaver SLA, the control auditors test most often |
| Flagged admin roles and policy exclusion groups on standard users | Least privilege review and access creep detection |
| Refused to grade data from a failed collection | Data freshness checks in a reporting pipeline |
| Kept a run history | Trend reporting for leadership |

**What I would change for production**

- Pull the roster from the real HR system instead of a CSV
- Use certificate authentication for the Graph app instead of a client secret
- Use a dedicated read only Okta service account, with the token bound to a network zone
- Raise the dormant threshold from the lab value of 14 days to 90 days
- Include PIM eligible assignments, not only active ones
- Schedule the scripts and alert when a measure changes verdict

---

## Run it yourself

```
pip install -r requirements.txt
copy .env.example .env        (then fill in your own values)
python scripts\01_connection_test.py
python scripts\02_collect_identity_data.py
python scripts\03_build_dashboard.py
```

Open `output\iam_metrics_dashboard.html` in a browser.

Thresholds can be changed in `.env` without touching code: `DORMANT_DAYS`, `PROVISIONING_SLA_HOURS`, `DEPROVISIONING_SLA_HOURS`.

## Repo layout

```
iam_metrics_dashboard/
  README.md
  requirements.txt
  .env.example
  .gitignore
  scripts/        the three Python scripts
  input/          hr_roster.csv and account_exceptions.csv
  output/         raw CSVs, kpi_history.csv, and the dashboard HTML
  evidence/       KPI summary and findings for each run
  screenshots/    everything shown above
```

---

## Resume bullet

> Built a Python IAM posture dashboard tracking 6 KPIs across Microsoft Entra ID and Okta through the Microsoft Graph and Okta APIs; cut orphaned account false positives from 10 flagged to 1 by correlating against an HR roster and exceptions register, and used the findings to close a leaver account left enabled for 4.8 days and bring the next leaver to same day deprovisioning.

## Interview version (Problem, Solution, Impact, Learning)

- **Problem:** Two identity platforms and no single view of whether accounts were owned, protected, and removed on time.
- **Solution:** Three Python scripts that collect from both APIs, correlate against HR, score six KPIs, and write a dashboard plus audit evidence.
- **Impact:** Went from 0 of 6 measures meeting target to 2 of 6 in one remediation pass, with every change traceable in a run history.
- **Learning:** The hardest bugs were not in the math. One was a rule that flagged almost everyone, and one was a report that showed old data with a new timestamp. Both taught me that a dashboard is only useful if people can trust it.
