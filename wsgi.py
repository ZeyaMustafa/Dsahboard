"""Production WSGI entry point.

Run with Gunicorn (recommended):
    gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 120 wsgi:application

Or with uWSGI:
    uwsgi --http :8000 --wsgi-file wsgi.py --callable application --processes 2 --threads 4

Environment variables to configure before starting:
    SECRET_KEY          Required in production. Use a long random string.
    HTTPS               Set to '1' when serving over HTTPS.
    FLASK_DEBUG         Set to '1' only for local development (never in production).
    HOST / PORT         Bind address for direct app.run() development use.
    SALES_CSV_PATH      Override the default sample CSV path.
    GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET
    FACEBOOK_OAUTH_CLIENT_ID / FACEBOOK_OAUTH_CLIENT_SECRET
    LIVE_SQL_CACHE_TTL_SECONDS      (default 120)
    AI_DECISION_CACHE_TTL_SECONDS   (default 300)
    SQL_SERVER_DEFAULT_LOOKBACK_DAYS (default 90)
"""
from app import app, _startup

_startup()

application = app
