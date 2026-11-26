# Piso Radar

Rental-listing alerts for personal use. A cron job collects listings from several property portals, filters them by each profile's criteria (city, price, rooms), stores them in SQLite and sends a push summary through [ntfy](https://ntfy.sh). A small Flask web app lets each profile browse new listings, favourites and discarded ones, with filters and change tracking (price drops on favourites).

- Python, Flask, SQLite, requests
- Profiles with separate logins (hashed passwords, login rate limiting, CSRF-style header check on actions)
- Collectors for Fotocasa, pisos.com, Habitaclia (Spain) and MyHome.ie (Ireland), with polite delays and per-source intervals
- 400+ automated tests (pytest)

*Personal project, designed by me and built with AI-assisted development (Claude Code). Code comments are in Spanish.*

## Run

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # set FLASK_SECRET_KEY and the ntfy topics
venv/bin/python perfiles.py clave la-rioja --generar
venv/bin/python main.py         # one collection pass (run it from cron)
venv/bin/python app.py          # web panel on :5051
venv/bin/pytest
```

Check each portal's terms of use before running the collectors.
