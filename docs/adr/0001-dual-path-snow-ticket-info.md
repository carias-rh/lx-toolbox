# Dual-path ServiceNow ticket info gathering (API + DOM fallback)

The SNOW AI processor gathers ticket information via two paths: a fast REST API path using `ServiceNowAPIClient` when credentials are configured, and the original Selenium DOM-scraping path when they are not. We chose this over a clean API-only migration because the API token is currently restricted — not all LX team members have access. Removing the DOM path would break the tool for colleagues without the token.

## Considered Options

- **API-only**: simpler code, single path. Rejected because it would gate the entire tool on API access that most of the team doesn't have yet.
- **DOM-only (status quo)**: no new dependencies. Rejected because it leaves ~12s of avoidable Selenium waits and fragile shadow-DOM piercing per ticket for those who do have API access.
- **Dual-path with auto-detection** (chosen): credential presence in `.env` automatically activates the API path. Zero configuration beyond having the credentials. The DOM path remains the default for everyone else.

## Consequences

- Two code paths through `get_snow_info()` and journal extraction must be kept in sync when ticket field semantics change.
- When API access is eventually extended to the full team, the DOM path can be removed and this ADR superseded.
