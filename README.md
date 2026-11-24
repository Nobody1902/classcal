# ClassCal

An iCalendar subscription endpoint for an Easistent school timetable. It runs as a single FastAPI process with no database or background scheduler; updates happen only when a calendar client requests the feed.

## Run it

```bash
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Subscribe with:

```
http://127.0.0.1:8000/calendar.ics?start=2026-11-23&end=2026-12-20
```

Credentials come from `USERNAME` and `PASSWORD` in `.env`, or from the environment. `CLASSCAL_USERNAME` / `CLASSCAL_PASSWORD` take precedence.

## NixOS module

The flake exports `packages.<system>.default` and `nixosModules.default`. The module creates a system user, stores state in `/var/lib/classcal`, binds to `127.0.0.1:8000`, runs one Uvicorn worker, and does not open the firewall.

Create the credentials file outside the Nix store:

```bash
sudo tee /etc/classcal.env >/dev/null <<'EOF'
CLASSCAL_USERNAME=your-username
CLASSCAL_PASSWORD=your-password
EOF
sudo chmod 0600 /etc/classcal.env
```

Keep the file root-owned with mode `0600`; systemd reads it before switching to the service user.

Then add the module to your NixOS configuration:

```nix
{
  inputs.classcal.url = "path:/home/nobody1902/Projects/classcal";

  outputs = { self, nixpkgs, classcal, ... }: {
    imports = [ classcal.nixosModules.default ];

    services.classcal = {
      enable = true;
      environmentFile = "/etc/classcal.env";
      settings = {
        TIMEZONE = "Europe/Ljubljana";
        SCHOOL_YEAR_START = "2026-09-01";
        SCHOOL_YEAR_END = "2027-06-30";
      };
    };
  };
}
```

| Option | Default | Purpose |
| --- | --- | --- |
| `enable` | `false` | Enable the service |
| `package` | flake package | Override the package, e.g. to use your own nixpkgs |
| `user` / `group` | `classcal` | Service account |
| `stateDir` | `/var/lib/classcal` | JSON cache and generated calendars |
| `listenAddress` | `127.0.0.1` | Uvicorn `--host` |
| `port` | `8000` | Uvicorn `--port` |
| `environmentFile` | `null` | File with `CLASSCAL_USERNAME` and `CLASSCAL_PASSWORD` |
| `settings` | `{ }` | Extra `config.py` settings without the `CLASSCAL_` prefix |

`DATA_DIR`, `TIMETABLE_DIR`, `CALENDAR_DIR`, `USERNAME`, and `PASSWORD` are reserved and must be set through `stateDir` and `environmentFile` instead. Check the flake with `nix flake check`.

## How updates are scheduled

There is **no polling job, cron, or periodic refresh**. A request to `/calendar.ics` triggers all work. The calendar app (Google Calendar, Apple Calendar, etc.) decides how often to fetch the subscription URL — that polling interval is what determines how soon a change becomes visible.

For each Monday–Sunday week overlapping the requested date range, the timetable manager decides whether the cached week can be reused:

| Week relative to today (Europe/Ljubljana) | Cache lifetime | Behavior |
| --- | --- | --- |
| Past | Never expires | A valid cached week is reused forever and never fetched again. A missing past week is fetched once. |
| Current week | 1 hour | Refreshed once `now >= fetched_at + 1h`. |
| Next week | 3 hours | Refreshed once `now >= fetched_at + 3h`. |
| 2–4 weeks ahead | 12 hours | Refreshed once `now >= fetched_at + 12h`. |
| 5+ weeks ahead | 24 hours | Refreshed once `now >= fetched_at + 24h`. |

A week's TTL is chosen from the Monday of the current week, so every day in that week shares the same policy. TTLs can be changed with `CLASSCAL_CACHE_TTL_CURRENT`, `CLASSCAL_CACHE_TTL_NEXT`, `CLASSCAL_CACHE_TTL_NEAR_FUTURE`, and `CLASSCAL_CACHE_TTL_FUTURE` (seconds).

This policy keeps requests to Easistent low: future weeks change rarely, past weeks cannot change, and the current week is checked most often.

## Request flow

1. **Validate** `start` and `end` (ISO dates, `start <= end`, inside the configured school year; otherwise HTTP 422/400).
2. **Determine weeks** — every Monday whose week overlaps the requested range.
3. **Resolve each week** under a per-week lock:
   - Load `data/timetable/<monday>.json` and validate it.
   - Fresh cache → reuse it, no HTTP request.
   - Missing → fetch the full week once from Easistent (including past weeks). Expired → fetch if a TTL applies; past weeks with a valid cache never expire.
   - On a successful fetch, validate the response and atomically write the JSON file.
   - Fetch/validation failure with an existing valid cache → keep the old data and mark the week as stale.
   - Fetch/validation failure without any valid cache → HTTP 503.
   - The cache is checked again after acquiring the lock, so simultaneous requests for the same week cause only one fetch.
4. **Combine events** from all resolved weeks, remembering each week's fetch time and stale status.
5. **Check the generated-calendar cache** using a fingerprint of the requested range, generator version, timezone, source weeks, their fetch times and stale flags. A matching `.ics` file plus metadata is reused; otherwise a new calendar is generated and atomically saved.
6. **Respond** with `text/calendar`, an `ETag` (the quoted fingerprint), and `Cache-Control: no-cache`. If `If-None-Match` matches the ETag, return `304 Not Modified`.

The timetable manager is always consulted before the calendar cache. An expired weekly cache can therefore never be hidden by an old generated calendar.

## Stale data

The scraper discovers the current timetable server action ID from the page's JavaScript chunks and caches it in the process. If Easistent rejects it with HTTP 409, the ID is rediscovered and the request is retried, so a site deployment cannot silently pin the app to stale data.

If Easistent is unavailable (network error, login failure, rate limiting, bad response), previously cached weeks are still served. The generated calendar then contains:

- a calendar-level `DESCRIPTION` warning,
- `X-TIMETABLE-STALE:TRUE`,
- `X-TIMETABLE-STALE-WEEKS` listing the affected Mondays,
- a note in the description of each affected event.

The warning disappears automatically on the next successful refresh, because the fingerprint changes when a week's stale flag or fetch time changes.

## Storage

- `data/timetable/<monday>.json` — source of truth, one file per week. Contains `metadata` (version, week start, fetch time) plus the original Easistent `schedule` and `events`, including unknown fields.
- `data/calendars/<key>.ics` and `<key>.json` — disposable generated calendars and metadata. `<key>` is a SHA-256 hash of the requested date range.

All writes go to a temporary file in the same directory and are atomically replaced. Malformed cache files are renamed to `<name>.corrupt-<reason>-<timestamp>.json` and treated as cache misses. Both directories are excluded from version control, and can be deleted to force a full rebuild.

## Calendar contents

- Times are interpreted in `Europe/Ljubljana` and emitted with a VTIMEZONE component.
- Events use stable UIDs (`<slug>@easistent`) and are deduplicated by UID. Only events whose date falls inside the requested range are included.
- `type: "holiday"` events are omitted.
- Graded events (those with an `evaluation` object) are marked with a `✱` prefix in the summary, `CATEGORIES:EXAM`, and evaluation details in the description. Their normal color is left unchanged.

## Configuration

All settings live in `config.py` and can be overridden through the environment (or `.env`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLASSCAL_TIMEZONE` | `Europe/Ljubljana` | Timezone for timetable times and TTL calculation |
| `CLASSCAL_SCHOOL_YEAR_START` | `2026-09-01` | Earliest accepted date |
| `CLASSCAL_SCHOOL_YEAR_END` | `2027-06-30` | Latest accepted date |
| `CLASSCAL_DATA_DIR` | `data` | Base data directory |
| `CLASSCAL_TIMETABLE_DIR` | `data/timetable` | Weekly JSON cache |
| `CLASSCAL_CALENDAR_DIR` | `data/calendars` | Generated calendar cache |
| `CLASSCAL_CACHE_TTL_*` | `3600`, `10800`, `43200`, `86400` | Current, next, near-future, future TTLs in seconds |
| `CLASSCAL_ICAL_PRODID` | `-//Easistent Timetable//EN` | Calendar PRODID |

Relative paths are resolved against the project directory.

## Deployment note

Locks are process-local. Run exactly **one** Uvicorn worker (`--workers 1`); multiple workers could fetch the same week concurrently. No Redis, database, or task queue is needed.

## Development

```bash
pytest
ruff check .
pyright
```
