"""Profile fields: allowed values, and turning what people type into canonical stored values.

`normalize_changes` is pure: given raw field values and the reference data it needs, it returns
storage-ready values or raises ProfileInvalidError listing every field's problem at once, so a
form can show all errors together and nothing is half-saved.
"""

import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

from app.domain.contact import (
    ContactRejectedError,
    normalize_linkedin,
    normalize_person_name,
    normalize_phone,
)
from app.domain.locations import LocationIndex
from app.domain.roles import RoleRejectedError, normalize_role

WorkAuthorization = Literal["citizen", "permanent_resident", "work_permit", "needs_sponsorship"]
NoticePeriod = Literal["immediate", "1_week", "2_weeks", "1_month", "2_months", "3_months"]
SalaryPeriod = Literal["month", "year"]

LABELS: dict[str, dict[str, str]] = {
    "work_authorization": {
        "citizen": "Citizen",
        "permanent_resident": "Permanent resident",
        "work_permit": "Have a work permit",
        "needs_sponsorship": "Need visa sponsorship",
    },
    "notice_period": {
        "immediate": "Can start immediately",
        "1_week": "1 week",
        "2_weeks": "2 weeks",
        "1_month": "1 month",
        "2_months": "2 months",
        "3_months": "3 months",
    },
    "salary_period": {"month": "per month", "year": "per year"},
}
for _name, _literal in (
    ("work_authorization", WorkAuthorization),
    ("notice_period", NoticePeriod),
    ("salary_period", SalaryPeriod),
):
    assert set(LABELS[_name]) == set(get_args(_literal)), f"labels out of sync for {_name}"


class ProfileInvalidError(ValueError):
    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = dict(errors)


@dataclass(frozen=True)
class ProfileRules:
    locations: LocationIndex
    phone_region: str


def public_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Use a public website address, like https://yourname.dev.")
    return url


def _roles(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        if not value.strip():
            continue
        title = normalize_role(value)
        if title.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(title)
    return cleaned


def _places(values: list[str], locations: LocationIndex) -> list[str]:
    keys: list[str] = []
    unknown = []
    for value in values:
        if not value.strip():
            continue
        place = locations.resolve(value)
        if place is None:
            unknown.append(value.strip())
        elif place.key not in keys:
            keys.append(place.key)
    if unknown:
        listed = ", ".join(f"'{u}'" for u in unknown)
        raise ValueError(
            f"We couldn't find {listed}. Pick a city, a country or Remote from the suggestions."
        )
    return keys


def _place(value: str, locations: LocationIndex) -> str:
    place = locations.resolve(value)
    if place is None:
        raise ValueError(f"We couldn't find '{value.strip()}'. Pick a place from the suggestions.")
    return place.key


def normalize_changes(changes: Mapping[str, Any], rules: ProfileRules) -> dict[str, Any]:
    """Storage-ready column values for the given changes. `None` clears a field."""
    normalizers: dict[str, Callable[[Any], dict[str, Any]]] = {
        "name": lambda v: {"name": normalize_person_name(v) if v else ""},
        "github_username": lambda v: {"github_username": v or None},
        "portfolio_url": lambda v: {"portfolio_url": public_url(str(v)) if v else None},
        "phone": lambda v: {"phone": normalize_phone(v, rules.phone_region) if v else None},
        "location": lambda v: {"location": _place(v, rules.locations) if v else None},
        "linkedin": lambda v: {"linkedin": normalize_linkedin(v) if v else None},
        "work_authorization": lambda v: {"work_authorization": v},
        "notice_period": lambda v: {"notice_period": v},
        "salary": lambda v: _salary(v, rules.locations),
        "target_roles": lambda v: {"target_roles": _roles(v or [])},
        "target_locations": lambda v: {"target_locations": _places(v or [], rules.locations)},
        "match_threshold": lambda v: {"match_threshold": v},
        "calm_mode": lambda v: {"calm_mode": v},
    }
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, raw in changes.items():
        normalize = normalizers.get(name)
        if normalize is None:
            errors[name] = "This field can't be changed here."
            continue
        try:
            values.update(normalize(raw))
        except (ValueError, ContactRejectedError, RoleRejectedError) as exc:
            errors[name] = str(exc)
    if errors:
        raise ProfileInvalidError(errors)
    return values


def _salary(value: Mapping[str, Any] | None, locations: LocationIndex) -> dict[str, Any]:
    if value is None:
        return {"salary_amount": None, "salary_currency": None, "salary_period": None}
    currency = str(value["currency"]).upper()
    if currency not in locations.currencies:
        raise ValueError(
            f"'{currency}' isn't a currency code we know. Use one like PKR, USD or EUR."
        )
    return {
        "salary_amount": int(value["amount"]),
        "salary_currency": currency,
        "salary_period": value["period"],
    }
