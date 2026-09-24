"""The runnable Stardust Core app: ``uvicorn stardust_core.main:app``.

Kept separate from ``api`` so that importing the API (tests, embedding) has no side
effects. Building the app reads the environment and opens the database.
"""

from .api import create_app

app = create_app()
