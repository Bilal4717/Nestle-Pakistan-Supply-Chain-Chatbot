"""
Vercel's Python runtime looks for a WSGI-compatible `app` object in this file.
This just imports the real Flask app from the project root -- no logic here,
this is purely the adapter Vercel needs to route requests to it.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (Flask app instance, used as the WSGI entrypoint)
