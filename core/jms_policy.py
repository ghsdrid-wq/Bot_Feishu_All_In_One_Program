"""Persistent allow/block policy for JMS user-changing commands."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


POLICY_VERSION = 1
POLICY_FILENAME = "jms_user_policy.json"
POLICY_KEYS = ("blocked_prefixes", "exempt_codes")
_POLICY_LOCK = threading.RLock()
_PREFIX_RE = re.compile(r"^[0-9A-Z]{2,20}$")
_CODE_RE = re.compile(r"^[0-9A-Z]{5,20}$")


class PolicyError(RuntimeError):
    """Raised when the policy cannot be safely read or written."""


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    exempt: bool = False
    matched_prefix: str | None = None
    error: str | None = None
    reason: str = "ALLOW_DEFAULT"


def default_policy_path() -> str:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return str(base / POLICY_FILENAME)


def normalize_entry(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _normalize_list(values: Iterable[object], kind: str) -> list[str]:
    pattern = _PREFIX_RE if kind == "blocked_prefixes" else _CODE_RE
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = normalize_entry(raw)
        if not value or value in seen or not pattern.fullmatch(value):
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def empty_policy() -> dict:
    return {
        "version": POLICY_VERSION,
        "blocked_prefixes": [],
        "exempt_codes": [],
    }


def validate_policy(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise PolicyError("Policy root must be a JSON object")
    policy = empty_policy()
    for key in POLICY_KEYS:
        values = raw.get(key, [])
        if not isinstance(values, list):
            raise PolicyError(f"Policy field {key} must be a list")
        policy[key] = _normalize_list(values, key)
    return policy


def _write_unlocked(path: str, policy: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise PolicyError(f"Cannot save JMS policy: {exc}") from exc


def save_policy(policy: dict, path: str | None = None) -> dict:
    target = path or default_policy_path()
    clean = validate_policy(policy)
    with _POLICY_LOCK:
        _write_unlocked(target, clean)
    return clean


def ensure_policy(path: str | None = None,
                  legacy_blocked: Iterable[object] = ()) -> dict:
    target = path or default_policy_path()
    with _POLICY_LOCK:
        if not os.path.exists(target):
            policy = empty_policy()
            policy["blocked_prefixes"] = _normalize_list(
                legacy_blocked, "blocked_prefixes")
            _write_unlocked(target, policy)
            return policy
    return load_policy(target)


def load_policy(path: str | None = None) -> dict:
    target = path or default_policy_path()
    with _POLICY_LOCK:
        try:
            text = Path(target).read_text(encoding="utf-8")
            return validate_policy(json.loads(text))
        except FileNotFoundError as exc:
            raise PolicyError(f"JMS policy file is missing: {target}") from exc
        except (OSError, json.JSONDecodeError, PolicyError) as exc:
            if isinstance(exc, PolicyError):
                raise
            raise PolicyError(f"Cannot read JMS policy: {exc}") from exc


def parse_bulk_entries(text: str, kind: str) -> tuple[list[str], list[str], list[str]]:
    if kind not in POLICY_KEYS:
        raise ValueError(f"Unknown policy list: {kind}")
    pattern = _PREFIX_RE if kind == "blocked_prefixes" else _CODE_RE
    tokens = [normalize_entry(value) for value in re.split(r"[\s,;]+", text or "")]
    valid: list[str] = []
    invalid: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for value in (token for token in tokens if token):
        if not pattern.fullmatch(value):
            invalid.append(value)
        elif value in seen:
            duplicates.append(value)
        else:
            seen.add(value)
            valid.append(value)
    return valid, invalid, duplicates


def evaluate_policy(staff_code: object, policy: dict) -> PolicyDecision:
    code = normalize_entry(staff_code)
    clean = validate_policy(policy)
    if not _CODE_RE.fullmatch(code):
        return PolicyDecision(
            allowed=False,
            error=f"Invalid staff code: {code or '<empty>'}",
            reason="INVALID_CODE",
        )

    # An exemption is part of the prefix-block rule, not a competing allow
    # rule.  Decide the exact exemption first so a future early-return in the
    # prefix branch cannot accidentally block an exempt code.
    if code in set(clean["exempt_codes"]):
        matching = sorted(
            (prefix for prefix in clean["blocked_prefixes"]
             if code.startswith(prefix)),
            key=len,
            reverse=True,
        )
        return PolicyDecision(
            allowed=True,
            exempt=True,
            matched_prefix=matching[0] if matching else None,
            reason="ALLOW_EXEMPT",
        )

    matching = sorted(
        (prefix for prefix in clean["blocked_prefixes"] if code.startswith(prefix)),
        key=len,
        reverse=True,
    )
    matched = matching[0] if matching else None
    if matched:
        return PolicyDecision(
            allowed=False,
            matched_prefix=matched,
            reason="BLOCKED_PREFIX",
        )
    return PolicyDecision(allowed=True, reason="ALLOW_DEFAULT")


def evaluate_code(staff_code: object, path: str | None = None) -> PolicyDecision:
    try:
        policy = load_policy(path)
    except PolicyError as exc:
        return PolicyDecision(
            allowed=False, error=str(exc), reason="POLICY_ERROR")
    return evaluate_policy(staff_code, policy)
