"""Fehler, die Auftragsverwaltung, Sperrclient und Agentenausführung gemeinsam verwenden."""


class OperationsError(Exception):
    """Basisklasse aller vertraglich definierten Fehler."""


class LeaseLost(OperationsError):
    """Der Auftragsbesitz ist abgelaufen oder an einen anderen Worker übergegangen.

    Der Ausführer SHALL danach keine weiteren Schreibschritte beginnen.
    """


class TransitionRejected(OperationsError):
    def __init__(self, from_state: str, to_state: str, reason: str):
        super().__init__(f"Übergang {from_state} -> {to_state} abgelehnt: {reason}")
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason


class TargetLocked(OperationsError):
    """Die App-/Umgebungs-Sperre gehört einer anderen Operation (Konfliktregel)."""

    def __init__(self, app_id: str, environment: str, operation_id: str, owner_ref: str):
        super().__init__(f"gesperrt durch Operation {operation_id}")
        self.app_id = app_id
        self.environment = environment
        self.operation_id = operation_id
        self.owner_ref = owner_ref


class UnresolvedActions(OperationsError):
    """Abgelaufener Sperrbesitz mit ungeklärter externer Änderung: keine blinde Neuvergabe."""

    def __init__(self, app_id: str, environment: str, operation_id: str, action_ids: list[str]):
        super().__init__(
            f"Sperre {app_id}/{environment} abgelaufen, aber Operation {operation_id} hat "
            f"ungeklärte Aktionen {', '.join(action_ids)}; zuerst abgleichen "
            f"(ops actions resolve)"
        )
        self.app_id = app_id
        self.environment = environment
        self.operation_id = operation_id
        self.action_ids = action_ids


class LockNotHeld(OperationsError):
    """Erneuerung, Besitzprüfung, Freigabe oder Schreibzugriff ohne gültigen Sperrbesitz."""


class NestedTargetLock(OperationsError):
    """Workflow-Dispatch, während die eigene Operation die Ziel-Sperre hält.

    Der dispatchte Workflow erwirbt die Sperre selbst; hielte der Agent sie weiter,
    würde der Workflow 15 Minuten warten und dann abbrechen.
    """


class SchemaMissing(OperationsError):
    def __init__(self) -> None:
        super().__init__(
            "Datenbankschema fehlt. Zuerst den Workflow .github/workflows/ops-migrate.yml "
            "ausführen (oder lokal: ops migrate)."
        )


class SchemaVersionMismatch(OperationsError):
    def __init__(self, expected: int, actual: int | None):
        super().__init__(
            f"Schema-Version passt nicht: erwartet {expected}, vorhanden {actual}. "
            "Migration mit ops migrate ausführen bzw. Helm-Hook-Job prüfen."
        )
        self.expected = expected
        self.actual = actual

    @classmethod
    def sdk(cls, message: str) -> "SchemaVersionMismatch":
        exc = cls.__new__(cls)
        OperationsError.__init__(exc, f"Checkpoint-Schema: {message}")
        exc.expected, exc.actual = None, None
        return exc


class DatabaseUnavailable(OperationsError):
    """PostgreSQL ist nicht erreichbar."""
