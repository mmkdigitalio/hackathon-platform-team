"""Prüfung von Ereignissen gegen platform/schemas/events/event.schema.json."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from api.catalog import find_platform_root


def schema_path() -> Path:
    root = find_platform_root()
    if root is None:
        raise ValueError("platform/ nicht gefunden; OPS_PLATFORM_ROOT setzen")
    return root / "schemas" / "events" / "event.schema.json"


def validator() -> Draft202012Validator:
    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors_for(instance: dict, v: Draft202012Validator | None = None) -> list[str]:
    """Fehlermeldungen mit benanntem Feld, z. B. ``deliveryId: ...``."""
    v = v or validator()
    out = []
    for error in sorted(v.iter_errors(instance), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in error.absolute_path) or _missing_field(error) or "<root>"
        out.append(f"{field}: {error.message}")
    return out


def _missing_field(error) -> str | None:
    if error.validator == "required":
        # "'deliveryId' is a required property"
        return error.message.split("'")[1] if "'" in error.message else None
    return None


def validate_files(files: list[str], *, expect_invalid: bool = False) -> list[str]:
    """Prüft Ereignisdateien; ungültige Beispiele tragen die Hülle {expectedField, event}."""
    v = validator()
    failures = []
    for name in files:
        doc = json.loads(Path(name).read_text(encoding="utf-8"))
        expected_field = doc.get("expectedField") if "event" in doc else None
        errors = errors_for(doc["event"] if expected_field else doc, v)
        if expect_invalid and not errors:
            failures.append(f"{name}: hätte abgelehnt werden müssen")
        elif expect_invalid and expected_field and not any(e.startswith(expected_field) for e in errors):
            failures.append(f"{name}: abgelehnt, aber nicht wegen {expected_field}: {errors}")
        elif not expect_invalid and errors:
            failures.extend(f"{name}: {e}" for e in errors)
        elif expect_invalid:
            print(f"{name}: abgelehnt wie erwartet ({errors[0]})")
        else:
            print(f"{name}: gültig")
    return failures
