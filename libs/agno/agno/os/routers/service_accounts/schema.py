"""Pydantic request/response models for the service accounts API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from agno.db.schemas.service_accounts import SERVICE_ACCOUNT_PRINCIPAL_PREFIX
from agno.os.service_accounts import (
    DEFAULT_EXPIRY_DAYS,
    is_valid_service_account_name,
)


class ServiceAccountCreate(BaseModel):
    name: str = Field(
        ...,
        max_length=63,
        description="Machine identity name (lowercase slug), e.g. 'claude-code' or 'github-actions'",
    )
    scopes: Optional[List[str]] = Field(
        default=None,
        description="Scopes granted to the token. Defaults to run and read scopes: "
        "agents:run, teams:run, workflows:run, sessions:read",
    )
    expires_in_days: Optional[int] = Field(
        default=DEFAULT_EXPIRY_DAYS,
        ge=1,
        le=3650,
        description=f"Days until the token expires (default: {DEFAULT_EXPIRY_DAYS})",
    )
    never_expires: bool = Field(
        default=False,
        description="Mint a non-expiring token. Must be set explicitly; overrides expires_in_days.",
    )
    allow_privileged_scopes: bool = Field(
        default=False,
        description="Required to grant privileged scopes: any write or delete action, the admin scope, "
        "or any service_accounts scope. Privileged tokens must be deliberate, never accidental.",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not is_valid_service_account_name(v):
            raise ValueError(
                "Name must be a lowercase slug: start with a letter or digit, "
                "then letters, digits, '_' or '-' (max 63 chars)"
            )
        return v


class ServiceAccountResponse(BaseModel):
    """Service account metadata. Never includes the token hash or plaintext."""

    id: str
    name: str
    principal: str = Field(..., description="The user_id attached to runs made with this token, e.g. 'sa:claude-code'")
    token_prefix: str = Field(..., description="First characters of the token, for display only")
    scopes: List[str]
    created_at: int
    expires_at: Optional[int] = None
    last_used_at: Optional[int] = None
    revoked_at: Optional[int] = None
    created_by: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServiceAccountResponse":
        return cls(
            id=data["id"],
            name=data["name"],
            principal=f"{SERVICE_ACCOUNT_PRINCIPAL_PREFIX}{data['name']}",
            token_prefix=data["token_prefix"],
            scopes=data.get("scopes") or [],
            created_at=data["created_at"],
            expires_at=data.get("expires_at"),
            last_used_at=data.get("last_used_at"),
            revoked_at=data.get("revoked_at"),
            created_by=data.get("created_by"),
        )


class ServiceAccountCreateResponse(ServiceAccountResponse):
    """Returned once, at creation. The token is never retrievable again."""

    token: str = Field(..., description="The plaintext token. Shown exactly once - store it securely now.")
