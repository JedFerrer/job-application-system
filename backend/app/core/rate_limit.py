"""Shared slowapi rate limiter.

A single ``Limiter`` instance is created here and attached to the app in
``main.py``. Auth endpoints decorate with ``@limiter.limit(...)`` to throttle
brute-force attempts (instructions §6).
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
