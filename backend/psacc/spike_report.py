"""Helpers for the spike report: strip anything personal before the owner shares it."""
import re

# substring match, or exact key for the short ones that would otherwise hit unrelated names
SENSITIVE_KEY = re.compile(r"token|secret|password|customer|position|latitude|longitude|geometry|coordinates"
                           r"|^(vin|lat|lon|lng)$|[a-z_]vin$", re.I)


def _replace(text: str, secrets: dict[str, str]) -> str:
    for value, placeholder in secrets.items():
        if value:
            text = text.replace(value, placeholder)
    return text


def sanitize(obj, secrets: dict[str, str]):
    """Redact sensitive keys (whole subtree) and replace known ids by placeholders in keys and values."""
    if isinstance(obj, dict):
        return {_replace(str(k), secrets): "REDACTED" if SENSITIVE_KEY.search(str(k)) else sanitize(v, secrets)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, secrets) for v in obj]
    if isinstance(obj, str):
        return _replace(obj, secrets)
    return obj


def find_keys(obj, names: set[str], path: str = "") -> dict:
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names:
                found[f"{path}/{k}"] = v
            found.update(find_keys(v, names, f"{path}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.update(find_keys(v, names, f"{path}[{i}]"))
    return found
