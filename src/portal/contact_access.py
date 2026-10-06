"""Exact contact identities for the bounded pilot, not consent or identity proof.

Every decision reads both canonical stores under the existing communication
gate. This module has no persistent effects, provider calls or grant mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .queue_bridge import _safe
from .store import CLIENT_ID

MAX_CONTACTS_PER_STORE = 5000
MAX_PROFILE_BYTES = 1024 * 1024
CHANNELS = {"email", "sms", "whatsapp"}
PURPOSES = {"sign_in", "verify_contact", "notification"}
PHONE_CONTEXT = "Phone kept for office contact until current service-message signoff and separate verification of a unique full number. Local/shared phones remain office contact only and do not grant another person's access."


class InventoryError(ValueError):
    """A bounded reason, never client/contact data in exception text."""

    def __init__(self, reason="contact_inventory_unavailable"):
        self.reason = reason
        super().__init__(reason)


def normalize_email(value):
    """ASCII trim/casefold only; no plus/dot/provider alias inference."""
    if not isinstance(value, str):
        raise ValueError("unsupported_email")
    value = value.strip()
    if not value.isascii():
        raise ValueError("unsupported_email")
    value = value.casefold()
    if (not 3 <= len(value) <= 254 or not value.isascii()
            or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value)):
        raise ValueError("unsupported_email")
    local, domain = value.rsplit("@", 1)
    if (len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local
            or "." not in domain or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in domain.split("."))):
        raise ValueError("unsupported_email")
    return value


def normalize_phone(value):
    """Supported pilot envelope: '+' and 8–15 digits, country digit nonzero.

    ASCII spaces, parentheses, hyphens and periods may format a full number.
    This does not validate allocation/reachability or infer a local country.
    """
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"[+0-9 ().-]+", value):
        raise ValueError("unsupported_phone")
    value = re.sub(r"[ ().-]", "", value)
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
        raise ValueError("unsupported_phone")
    return value


def contact_input(contact):
    channel = "email" if isinstance(contact, str) and "@" in contact else "sms"
    return channel, normalize_email(contact) if channel == "email" else normalize_phone(contact)


def rate_identity(contact):
    """Bounded, opaque limiter key; formatting cannot reset a destination limit."""
    try:
        channel, value = contact_input(contact)
    except ValueError:
        # Unsupported inputs have one bucket, never a truncated valid identity.
        channel, value = "unsupported", ""
    return hashlib.sha256(json.dumps([channel, value], separators=(",", ":")).encode()).hexdigest()


def enrollment_contacts(scope, email, phone):
    """Generic office policy, not consent, contact control or recovery authority.

    Unique email permits a shared office phone. A phone-only duplicate requires
    assisted contact correction; no existing person's name/count is returned.
    Unsupported local numbers remain office context, never automated lookup.
    Caller holds the installation gate through subsequent enrollment effects.
    """
    if not isinstance(email, str) or not isinstance(phone, str):
        raise ValueError("Enter an email address and phone number as text.")
    email = normalize_email(email) if email.strip() else ""
    phone = phone.strip()
    if phone:
        try:
            phone = normalize_phone(phone)
        except ValueError:
            if (len(phone) > 64 or "+" in phone or not re.fullmatch(r"[0-9 ().-]+", phone)
                    or not 1 <= len(re.sub(r"\D", "", phone)) <= 15):
                raise ValueError("Use a full country-code phone number beginning with +, or a bounded local number for office contact only.") from None
    if scope is not None:
        rows = _inventory(scope)
        if email and _matches(rows, "email", email):
            raise ValueError("That contact cannot be used for a new portal identity. Ask authorized staff to review the existing contact or use a distinct safe email.")
        if phone and not email:
            try:
                number = normalize_phone(phone)
            except ValueError:
                number = None
            if number and (_matches(rows, "sms", number) or any(row.unsupported_phone for row in rows)):
                raise ValueError("That contact cannot be used for a new portal identity. Use a distinct safe email or assisted office intake.")
    return {"email": email, "phone": phone, **({"phone_access": "office_only", "phone_note": PHONE_CONTEXT} if phone else {})}


@dataclass(frozen=True)
class ContactIdentity:
    # Internal inventory; callers must never serialize it to a public route.
    store_kind: str
    client: str
    email: str | None
    phone: str | None
    unsupported_phone: bool = False


def _profiles(root, kind):
    identities = []
    if not root.is_dir():
        raise InventoryError()
    count = 0
    for folder in root.iterdir():
        count += 1
        if count > MAX_CONTACTS_PER_STORE:
            raise InventoryError("contact_inventory_limit")
        folder = _safe(folder)
        if not folder.is_dir() or not CLIENT_ID.fullmatch(folder.name):
            raise InventoryError()
        path = _safe(folder / "profile.json")
        if not path.is_file() or path.stat().st_size > MAX_PROFILE_BYTES:
            raise InventoryError()
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(profile, dict) or profile.get("id") != folder.name
                    or not isinstance(profile.get("email", ""), str)
                    or not isinstance(profile.get("phone", ""), str)):
                raise InventoryError()
            email = normalize_email(profile["email"]) if profile.get("email", "").strip() else None
            raw_phone = profile.get("phone", "")
            phone, unsupported = None, False
            if raw_phone.strip():
                try:
                    phone = normalize_phone(raw_phone)
                except ValueError:
                    # Preserve office-only local context. It cannot prove
                    # uniqueness for ANY automated phone destination.
                    unsupported = True
            identities.append(ContactIdentity(kind, folder.name, email, phone, unsupported))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            if isinstance(exc, InventoryError):
                raise
            raise InventoryError() from None
    return tuple(identities)


def _raw_inventory(scope):
    identities = []
    for kind, root in (("client", scope.data / "portal" / "clients"),
                       ("prospect", scope.data / "portal" / "prospects" / "clients")):
        root = _safe(root)
        if not root.exists():
            store_root = _safe(root.parent)
            if store_root.exists():
                if not store_root.is_dir():
                    raise InventoryError()
                # Main portal can be only a container for the optional
                # prospect store. Operational records with no client inventory
                # are not proof that a previously populated store is empty.
                if any(kind != "client" or entry.name != "prospects" for entry in store_root.iterdir()):
                    raise InventoryError()
            # A never-created canonical store is empty, not a damaged profile.
            continue
        identities.extend(_profiles(root, kind))
    return tuple(identities)


def _inventory(scope):
    import read_scope
    return read_scope.contact_once(("contact-inventory", str(scope.root)), lambda: _inventory_read(scope))


def _inventory_read(scope):
    from .promotion import eligible_inventory
    return eligible_inventory(scope, _raw_inventory(scope))


def inventory(scope):
    from .communication_consent import gate
    with gate(scope):
        return _inventory(scope)


def _matches(identities, channel, value):
    field = "email" if channel == "email" else "phone"
    return [row for row in identities if getattr(row, field) == value]


def _binding(scope, client, channel, destination, matches):
    # Same own-store/client/channel/destination digest used by consent grants.
    own = [str(scope.portal), client, channel, destination]
    group = sorted((row.store_kind, row.client) for row in matches)
    return {"destination_sha256": hashlib.sha256(json.dumps(own, separators=(",", ":")).encode()).hexdigest(),
            "contact_group_sha256": hashlib.sha256(json.dumps([channel, destination, group], separators=(",", ":")).encode()).hexdigest()}


def contact_eligibility(scope, store, client, channel, purpose="notification"):
    """Contact safety only. Consent remains the sole outbound authority.

    Result contains no other identity/name. Even eligible contact does not
    establish consent, wording, contact control or legal/client authority.
    """
    from .communication_consent import gate
    with gate(scope):
        try:
            scope.check(store, client)
            if channel not in CHANNELS or purpose not in PURPOSES:
                return {"eligible": False, "reason": "unsupported_contact_purpose"}
            rows = _inventory(scope)
            own = next((row for row in rows if (row.store_kind, row.client) == (scope.kind, client)), None)
            if own is None:
                return {"eligible": False, "reason": "contact_unavailable"}
            destination = own.email if channel == "email" else own.phone
            if destination is None:
                return {"eligible": False, "reason": "unsupported_destination"}
            if channel != "email" and any(row.unsupported_phone for row in rows):
                return {"eligible": False, "reason": "phone_inventory_requires_correction"}
            matches = _matches(rows, channel, destination)
            if len(matches) != 1:
                return {"eligible": False, "reason": "ambiguous_destination"}
            return {"eligible": True, "reason": "unique_contact", "channel": channel,
                    "destination": destination, **_binding(scope, client, channel, destination, matches)}
        except InventoryError as exc:
            return {"eligible": False, "reason": exc.reason}
        except (ValueError, OSError, UnicodeError, TypeError, LookupError):
            return {"eligible": False, "reason": "contact_inventory_unavailable"}


def lookup_contact(scope, contact):
    """Internal unique identity across BOTH stores; public response stays generic."""
    from .communication_consent import gate
    with gate(scope):
        try:
            channel, value = contact_input(contact)
            rows = _inventory(scope)
            if channel != "email" and any(row.unsupported_phone for row in rows):
                return {"matched": False, "reason": "phone_inventory_requires_correction"}
            matches = _matches(rows, channel, value)
            if len(matches) != 1:
                return {"matched": False, "reason": "ambiguous_destination" if matches else "contact_unavailable"}
            own = matches[0]
            return {"matched": True, "store_kind": own.store_kind, "client": own.client, "channel": channel}
        except InventoryError as exc:
            return {"matched": False, "reason": exc.reason}
        except (ValueError, OSError, UnicodeError, TypeError):
            return {"matched": False, "reason": "contact_inventory_unavailable"}


def utility_lookup(root, contact):
    """Historical office data only; no installation/authentication authority."""
    try:
        channel, value = contact_input(contact)
        rows = _profiles(_safe(root / "clients"), "utility")
        if channel != "email" and any(row.unsupported_phone for row in rows):
            return None
        matches = _matches(rows, channel, value)
        return matches[0].client if len(matches) == 1 else None
    except (ValueError, OSError, UnicodeError, TypeError):
        return None
