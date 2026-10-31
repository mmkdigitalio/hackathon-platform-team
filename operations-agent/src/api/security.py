"""Key- und Signaturprüfung mit konstanter Laufzeit sowie Maskierung in Logs."""

import hashlib
import hmac
import logging
import re

MASK = "***"
_QUERY_KEY = re.compile(r"([?&]key=)[^&\s]*", re.I)
_SECRET_HEADERS = {"x-api-key", "x-portal-code", "x-hub-signature-256", "x-hub-signature", "authorization"}


def key_matches(given: str | None, expected: str) -> bool:
    # Ohne konfigurierten Key wird nie angenommen.
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def github_signature_valid(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.encode(), expected.encode())


def mask_query(query: str) -> str:
    """Maskiert den Parameter key in einem Query-String ohne führendes ?."""
    return _QUERY_KEY.sub(r"\1" + MASK, "?" + query)[1:] if query else ""


def mask_headers(headers: dict) -> dict:
    return {k: (MASK if k.lower() in _SECRET_HEADERS else v) for k, v in headers.items()}


class SecretMaskingFilter(logging.Filter):
    """Ersetzt bekannte Secret-Werte und ?key= in allen Logzeilen."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = _QUERY_KEY.sub(r"\1" + MASK, message)
        for secret in self.secrets:
            masked = masked.replace(secret, MASK)
        if masked != message:
            record.msg, record.args = masked, None
        return True


def install_log_masking(secrets: list[str]) -> SecretMaskingFilter:
    log_filter = SecretMaskingFilter(secrets)
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(log_filter)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "ops"):
        logging.getLogger(name).addFilter(log_filter)
    return log_filter
