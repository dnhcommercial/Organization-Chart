# D&H Sécheron — Organization Chart

A Flask app that shows the company org chart, lets an admin update it from the
browser, and automatically keeps every employee's career history (promotions,
transfers, title changes) over the years.

## What's inside

| Page | URL | Who |
|---|---|---|
| Org chart (collapsible tree) | `/` | everyone |
| Directory with search + department filter | `/directory` | everyone |
| Employee profile + career timeline | `/employee/<id>` | everyone |
| Manage people (add / edit / move / exit / delete) | `/admin` | admin only |

**How history tracking works:** every role an employee holds is a row in
`role_event`. When you use **Record movement** (promotion, transfer, title
change), the app closes the current role on the effective date and opens a new
one — so the timeline on their profile builds itself. **Edit details** only
touches contact info and never rewrites history.

The app ships with 10 sample employees so the chart isn't blank on first run.
Delete them from **Manage** and add your real people.

## Run locally

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000  — admin password is "admin" by default
```

Locally it uses SQLite (`instance/orgchart.db`). No setup needed.

## Deploy on Render (one-time, ~10 minutes)

1. **Push this folder to a GitHub repo** (private is fine):
   ```bash
   git init && git add . && git commit -m "org chart"
   git remote add origin https://github.com/<you>/dnh-orgchart.git
   git push -u origin main
   ```

2. **In Render:** New → **Blueprint** → connect the repo. Render reads
   `render.yaml` and creates two things:
   - a **web service** running `gunicorn app:app`
   - a **Postgres database**, wired in automatically via `DATABASE_URL`

3. When prompted, **type a strong `ADMIN_PASSWORD`**. (`SECRET_KEY` is
   generated for you.)

4. Deploy. Your chart is live at `https://dnh-orgchart.onrender.com`
   (or whatever name you pick).

### Why Postgres and not just SQLite?

Render's free web services have an **ephemeral disk** — a SQLite file would be
wiped on every deploy or restart, and you'd lose your data. The blueprint
provisions Postgres so your data survives.

Two Render free-tier caveats to be aware of (check Render's current pricing
page, these change):
- Free web services **sleep after inactivity** and take ~30–60 s to wake on
  the first visit.
- Render's free Postgres has historically had a **time limit** before it must
  be upgraded to a paid plan (~$7/mo). For an HR tool you'll update for years,
  the small paid database is the safe choice. Alternatively, use a free
  external Postgres (e.g. Neon or Supabase) and paste its connection string
  into the `DATABASE_URL` environment variable on Render instead.

## Updating the chart day-to-day

1. Open `/admin` and sign in.
2. **New joiner** → *Add employee* (creates their "Hired" history entry).
3. **Promotion / transfer / new designation** → open the person → *Record
   movement*. Pick the type, effective date, new title/department/manager.
4. **Someone leaves** → *Mark exited*. They disappear from the chart, their
   team is re-attached to their manager, and their history is preserved.
5. **Mistake?** *Delete* removes a person and their history permanently.

No redeploys needed for any of this — it's all in the database.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ADMIN_PASSWORD` | Password for `/admin` | `admin` (change it!) |
| `SECRET_KEY` | Flask session signing | dev value (set in prod) |
| `DATABASE_URL` | Postgres connection string | SQLite locally |

## Customising

- Department list: `DEPARTMENTS` in `app.py`
- Movement types: `EVENT_TYPES` in `app.py`
- Branding (colors, fonts): `static/style.css` — brand red is `--red: #C8102E`
- Sample data: `seed_if_empty()` in `app.py` (only runs on an empty database)
