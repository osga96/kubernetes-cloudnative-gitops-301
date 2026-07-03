"""API demo del curso — configuración externalizada (12-factor / cloudnative)."""
from __future__ import annotations

import os
import random
import time

import redis
from flask import Flask, jsonify
from psycopg2 import connect

app = Flask(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
API_PORT = int(os.environ.get("PORT", "8081"))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "cloudnative-demo-api")
LAB_SLOW_SECONDS = float(os.environ.get("LAB_SLOW_SECONDS", "3"))


@app.get("/health")
def health():
    """Liveness: el proceso responde."""
    return jsonify(status="ok", service=SERVICE_NAME)


@app.get("/ready")
def ready():
    """Readiness: dependencias accesibles (Postgres + Redis)."""
    try:
        with connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        redis.from_url(REDIS_URL).ping()
    except OSError as exc:
        return jsonify(status="not_ready", error=str(exc)), 503
    return jsonify(status="ready", service=SERVICE_NAME)


@app.get("/work")
def work():
    delay = random.uniform(0.05, 0.35)
    time.sleep(delay)

    client = redis.from_url(REDIS_URL)
    hits = client.incr("lab:hits")

    with connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    return jsonify(hits=int(hits), delay_ms=round(delay * 1000, 1))


@app.get("/slow")
def slow():
    time.sleep(LAB_SLOW_SECONDS)
    return jsonify(status="slow", delay_seconds=LAB_SLOW_SECONDS)


@app.get("/fail")
def fail():
    return jsonify(error="simulated failure"), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=API_PORT)
