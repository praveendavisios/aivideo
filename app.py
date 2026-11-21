"""Mabix AI entry point.

Run:  python app.py            (http://localhost:5000)
The original pipeline functions remain importable from here for any
existing scripts, e.g. `from app import generate_news_short`.
"""
import os

from mabix import create_app
from mabix.pipelines.legacy import *  # noqa: F401,F403  (backward-compatible imports)

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("MABIX_HOST", "0.0.0.0"), port=int(os.environ.get("MABIX_PORT", "5000")), debug=False)
