from __future__ import annotations

from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from sd_agent.adapters.teable import TeableAdapterError, TeableClient, TeableFilter
from sd_agent.auth.service import PersonIdentity, RoleScope


class PersonFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person_id: int
    name: str
    unit_id: int
    active: bool
    authz_version: int


class RoleFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person_id: int
    role: str
    scope_unit_id: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    active: bool


ModelT = TypeVar("ModelT", bound=BaseModel)


class TeableIdentityStore:
    def __init__(self, teable: TeableClient) -> None:
        self._teable = teable

    async def get_person(self, person_id: int) -> PersonIdentity | None:
        records = await self._teable.list_records(
            "person",
            projection=("person_id", "name", "unit_id", "active", "authz_version"),
            filter_by=TeableFilter("person_id", "is", person_id),
            take=2,
        )
        if not records:
            return None
        if len(records) != 1:
            raise TeableAdapterError("TEABLE_IDENTITY_CONFLICT", retryable=False)
        fields = _validate(PersonFields, records[0].fields)
        return PersonIdentity(**fields.model_dump())

    async def get_active_roles(self, person_id: int, *, now: datetime) -> tuple[RoleScope, ...]:
        records = await self._teable.list_records(
            "role_assignment",
            projection=(
                "person_id",
                "role",
                "scope_unit_id",
                "valid_from",
                "valid_until",
                "active",
            ),
            filter_by=TeableFilter("person_id", "is", person_id),
            take=100,
        )
        roles: list[RoleScope] = []
        for record in records:
            fields = _validate(RoleFields, record.fields)
            if (
                fields.person_id == person_id
                and fields.active
                and (fields.valid_from is None or fields.valid_from <= now)
                and (fields.valid_until is None or fields.valid_until > now)
            ):
                roles.append(RoleScope(fields.role, fields.scope_unit_id))
        return tuple(roles)


def _validate(model: type[ModelT], fields: dict[str, Any]) -> ModelT:
    try:
        return model.model_validate(fields)
    except ValidationError as exc:
        raise TeableAdapterError("TEABLE_INVALID_RESPONSE", retryable=False) from exc
