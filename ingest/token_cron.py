"""Kite token cron helpers.

This module never stores secrets. It only checks environment state or verifies
an already supplied token through a caller-provided client.
"""
from __future__ import annotations

from ingest.kite import token_refresh_check


def refresh_tokens(check_only: bool = True) -> dict:
    status = token_refresh_check()
    status["check_only"] = check_only
    if not check_only:
        status["action"] = "manual_kite_login_required"
    return status
