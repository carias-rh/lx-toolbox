# Optimize SNOW AI processor: REST API fast-path, cookie pre-set, sleep elimination

## Problem Statement

The `snowai` Feedback processing flow is too slow. Per ticket, the LX Engineer waits ~20+ seconds of avoidable dead time: fixed `time.sleep()` calls, TrustArc cookie consent appearing on every run (browser profile is not persisted), a redundant `wait_for_site_to_be_ready()` check on ROL after login is already confirmed, and Selenium DOM-scraping of ServiceNow ticket fields that could be fetched instantly via the REST API. These delays compound across a queue of Feedbacks and make the tool feel sluggish.

## Solution

Introduce a REST API fast-path for ServiceNow ticket info gathering, pre-set the TrustArc consent cookie to skip the consent UI, bypass the redundant ROL site-ready check in the `snowai` flow, replace fixed `time.sleep()` calls with `WebDriverWait` conditions, and open the SNOW ticket via the direct `.do` form URL instead of a redirect. When API credentials are not available (not all LX team members have them yet), the existing DOM-scraping path remains the default — zero breakage for colleagues without the token.

## User Stories

1. As an LX Engineer with SNOW API credentials, I want Feedback ticket info fetched via the REST API, so that I skip ~12 seconds of Selenium DOM scraping and shadow-DOM piercing per ticket.
2. As an LX Engineer without SNOW API credentials, I want the tool to work exactly as before, so that I am not blocked by the API rollout.
3. As an LX Engineer, I want the TrustArc cookie consent to never appear, so that I don't waste 3-5 seconds dismissing it on every run.
4. As an LX Engineer, I want the SNOW ticket to open directly on the classic `.do` form, so that I land on the editable form instead of the workspace redirect (`hub.redhat.com/now/sow/record/...`).
5. As an LX Engineer, I want the ROL "Waiting for site to be ready..." check removed from the per-ticket flow, so that I don't wait 5-15 seconds on a login verification I already passed.
6. As an LX Engineer, I want the tool to wait for actual page content (course content wrapper) instead of an arbitrary header button XPath, so that the wait is as short as the page load, not a padded guess.
7. As an LX Engineer, I want fixed `time.sleep()` calls replaced with `WebDriverWait` conditions, so that waits finish as soon as the condition is met rather than always burning the full sleep duration.
8. As an LX Engineer, I want customer journal entries (Additional comments, Email received, Email sent) fetched via the REST API when available, so that the shadow-DOM iframe gymnastics and email-iframe expansion delays are skipped.
9. As an LX Engineer, I want a log line at startup telling me whether the API fast-path is active or not, so that I understand which mode the tool is running in.
10. As an LX Engineer, I want the SNOW ticket page still opened in the browser early (before ROL analysis), so that I can visually read the Learner's Feedback while the analysis runs.
11. As an LX Engineer, I want the stale table name in `servicenow_autoassign.py` corrected to `x_redha_rht_task`, so that the auto-assign module uses the current ServiceNow instance.

## Implementation Decisions

### New module: `ServiceNowAPIClient`

- A new class in a dedicated module under `lx_toolbox/core/`.
- Responsible for read-only REST API operations: fetching ticket fields and journal entries.
- Uses `requests.Session` with basic auth, same credential keys as `servicenow_autoassign.py` (`SNOW_INSTANCE_URL`, `SNOW_API_USER`, `SNOW_API_PASSWORD`).
- Defines `FEEDBACK_TABLE = "x_redha_rht_task"` as a constant — single source of truth for the table name.
- Public interface:
  - `get_ticket(ticket_number: str) -> dict` — fetches ticket fields (`description`, `contact_source`, `sys_id`, `number`) from `/api/now/table/{FEEDBACK_TABLE}`.
  - `get_journal_entries(sys_id: str) -> list[dict]` — fetches entries from `/api/now/table/sys_journal_field` filtered by `element_id={sys_id}`, returns list of dicts with `timestamp`, `author`, `type`, `text` keys matching the existing `get_customer_updates()` output shape.

### API/DOM auto-detection (ADR-0001)

- `SnowAIProcessor.__init__()` attempts to create a `ServiceNowAPIClient`. If credentials are missing, `self._snow_api` is set to `None`.
- `get_snow_info()` checks `self._snow_api`: if available, fetches via API then parses the description with the same regex logic; if not, uses the existing DOM-scraping path unchanged.
- Same branching for journal entries: API path calls `get_journal_entries()`, DOM path calls `get_customer_updates()`.
- A log line at startup indicates which mode is active.

### Direct `.do` ticket URL

- In `run()`, replace the `/api/redha/surl?n={snow_id}` navigation with a call to `navigate_to_ticket()`, which uses `x_redha_rht_task.do?sysparm_query=number={snow_id}`.
- Replace the 5-second `time.sleep()` in `navigate_to_ticket()` with a `WebDriverWait` for the form's description field to be present.

### TrustArc cookie pre-set

- After the first navigation to any `redhat.com` subdomain, call `driver.add_cookie()` to set the TrustArc consent cookie, preventing the consent banner from appearing.
- Reduce the fallback timeout in `accept_trustarc_cookies()` from 5 seconds to 2 seconds as a safety net.
- No browser profile persistence — SSO login is still required each run.

### ROL wait bypass in `snowai` flow

- `go_to_course()` currently calls `go_to_url()` (2s sleep) then `wait_for_site_to_be_ready()` (5-15s). In the `snowai` path, `prelogin_all()` already verified the ROL login.
- For `snowai`, bypass both: call `driver.get()` directly, then `WebDriverWait` for the `course__content-wrapper` element — this proves the course page loaded with content.
- `go_to_url()` and `wait_for_site_to_be_ready()` remain unchanged for other callers (QA, standalone lab operations).

### Sleep-to-WebDriverWait replacements

- `navigate_to_ticket()` 5s sleep → `WebDriverWait` for description form field.
- `navigate_to_feedback_queue()` 3s sleep → `WebDriverWait` for ticket list or "No records" text.
- `run()` post-SURL 3s sleep → eliminated (replaced by `navigate_to_ticket()` wait).
- `run()` post-ROL-navigation 2s sleeps → folded into the `course__content-wrapper` `WebDriverWait`.
- `open_jira_create_prefilled()` 5s sleep → `WebDriverWait` for Jira create button.

### Stale table name fix

- Replace `x_redha_red_hat_tr_x_red_hat_training` with `x_redha_rht_task` in `servicenow_autoassign.py` (3 occurrences).
- Import the `FEEDBACK_TABLE` constant from `ServiceNowAPIClient` so both modules share a single source of truth.

## Testing Decisions

### What makes a good test here

Tests should verify external behavior — that the right data comes back in the right shape, and that the tool completes its flow without errors — not implementation details like which HTTP method was called or which XPath was used. The existing `test-scripts/` pattern (standalone scripts that run against real services with human-readable output) is the prior art.

### Primary seam: `ServiceNowAPIClient`

- **Unit-testable**: mock `requests.Session` responses and verify that `get_ticket()` returns the expected dict shape, that `get_journal_entries()` returns entries with `timestamp`/`author`/`type`/`text` keys, and that errors (auth failure, ticket not found) are handled gracefully.
- **Integration script**: a new `test-scripts/test_snow_api.py` following the existing `test_lookup_user.py` pattern — instantiate the real client, fetch a known ticket, print the result.

### Supporting seam: `get_snow_info()` output contract

- **Integration script**: a new `test-scripts/test_snow_api_vs_dom.py` that runs both paths for the same ticket and compares the output dicts. This verifies the API path produces equivalent results to the DOM path.

### Not separately tested

Cookie pre-set, sleep replacements, ROL wait bypass — these are control-flow changes inside existing methods. They are verified by the overall flow completing successfully and faster.

## Out of Scope

- **Browser profile persistence**: decided against. SSO login is still required each session.
- **Posting replies via SNOW API**: the reply form pre-fill stays browser-based for human review.
- **Modifying `go_to_url()` or `wait_for_site_to_be_ready()` globally**: only the `snowai` code path is changed. QA and standalone lab operations are unaffected.
- **Jira API adoption**: Jira interactions remain browser-based.
- **ROL API adoption**: Guide Text and course navigation remain browser-based.
- **ServiceNow write operations via API**: only reads are covered.

## Further Notes

- **Expected time savings**: ~15-20 seconds per ticket (12s from API replacing DOM scraping + SNOW sleeps, 5-10s from ROL wait removal and sleep-to-WebDriverWait conversions, ~3s from cookie pre-set on first ticket).
- **ADR-0001** (`docs/adr/0001-dual-path-snow-ticket-info.md`) records the dual-path decision and its rationale. When API access is extended to the full LX team, the DOM path can be removed and the ADR superseded.
- **Table name `x_redha_red_hat_tr_x_red_hat_training`** in `servicenow_autoassign.py` is from a previous SNOW instance. Fixing it is part of this work but should be validated against the current `SNOW_INSTANCE_URL` before deploying.
