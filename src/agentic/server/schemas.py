"""Request/response schemas for the HTTP surface (provisional — Malik).

These are what Michael's frontend builds against. Changing any field is
a contract change — ping first.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1)

    # NOTE: deliberately no client-supplied config path — that would let a
    # request point the pipeline at arbitrary local files. Operators set
    # AGENTIC_CONFIG_PATH instead.
