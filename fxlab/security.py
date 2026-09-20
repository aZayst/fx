"""API-key guard for admin endpoints and the ack callback."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected = request.app.state.ctx.settings.api_key
    if not expected:  # auth disabled (local dev)
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
