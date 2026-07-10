"""Configurable capture allowlists, redaction, and artifact-reference policy."""

from __future__ import annotations

import copy
import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


DEFAULT_SECRET_PATTERNS = (
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*([^\s,;]+)",
    r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
)


@dataclass(frozen=True)
class CaptureDecision:
    accepted: bool
    event: dict[str, Any] | None
    reason: str = ""
    redaction_count: int = 0


@dataclass
class CapturePolicy:
    project_allowlist: tuple[str, ...] = ()
    project_denylist: tuple[str, ...] = ()
    path_allowlist: tuple[str, ...] = ()
    path_denylist: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...] = ("local_only",)
    redaction_enabled: bool = True
    redaction_patterns: tuple[str, ...] = DEFAULT_SECRET_PATTERNS
    artifact_mode: str = "references_only"
    artifact_max_size: int = 100 * 1024 * 1024
    _compiled_patterns: list[re.Pattern[str]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compiled_patterns = [re.compile(pattern) for pattern in self.redaction_patterns]
        if self.artifact_mode not in {"references_only", "drop", "preserve"}:
            raise ValueError(f"Unsupported artifact policy mode: {self.artifact_mode}")

    @classmethod
    def from_config(cls, value: dict[str, Any] | None) -> "CapturePolicy":
        config = value or {}
        redaction = config.get("redaction") or {}
        artifact = config.get("artifacts") or {}
        custom_patterns = tuple(str(item) for item in redaction.get("patterns") or ())
        patterns = DEFAULT_SECRET_PATTERNS + custom_patterns
        return cls(
            project_allowlist=_strings(config.get("project_allowlist")),
            project_denylist=_strings(config.get("project_denylist")),
            path_allowlist=_strings(config.get("path_allowlist")),
            path_denylist=_strings(config.get("path_denylist")),
            allowed_scopes=_strings(config.get("allowed_scopes")) or ("local_only",),
            redaction_enabled=bool(redaction.get("enabled", True)),
            redaction_patterns=patterns,
            artifact_mode=str(artifact.get("mode") or "references_only"),
            artifact_max_size=int(artifact.get("max_size_bytes", 100 * 1024 * 1024)),
        )

    def apply(self, event: dict[str, Any]) -> CaptureDecision:
        project = str(event.get("project") or "")
        source_path = _source_path(event)
        scope = str((event.get("privacy") or {}).get("scope") or "local_only")
        if scope not in self.allowed_scopes:
            return CaptureDecision(False, None, f"privacy_scope_not_allowed:{scope}")
        if self.project_allowlist and not _matches_any(project, self.project_allowlist):
            return CaptureDecision(False, None, "project_not_allowlisted")
        if _matches_any(project, self.project_denylist):
            return CaptureDecision(False, None, "project_denied")
        if self.path_allowlist and not _matches_any(source_path, self.path_allowlist):
            return CaptureDecision(False, None, "path_not_allowlisted")
        if _matches_any(source_path, self.path_denylist):
            return CaptureDecision(False, None, "path_denied")

        sanitized = copy.deepcopy(event)
        redactions = 0
        if self.redaction_enabled:
            sanitized, redactions = self._redact_value(sanitized)
        sanitized["artifacts"] = self._sanitize_artifacts(sanitized.get("artifacts") or [])
        payload = sanitized.setdefault("payload", {})
        if redactions:
            payload["redaction"] = {
                "schema_version": "rawmem.redaction.v1",
                "count": redactions,
                "policy": "configured_secret_patterns",
            }
        payload["capture_policy"] = {
            "schema_version": "rawmem.capture_policy.v1",
            "artifact_mode": self.artifact_mode,
        }
        return CaptureDecision(True, sanitized, redaction_count=redactions)

    def _redact_value(self, value: Any) -> tuple[Any, int]:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, list):
            output = []
            count = 0
            for item in value:
                clean, item_count = self._redact_value(item)
                output.append(clean)
                count += item_count
            return output, count
        if isinstance(value, dict):
            output = {}
            count = 0
            for key, item in value.items():
                clean, item_count = self._redact_value(item)
                output[key] = clean
                count += item_count
            return output, count
        return value, 0

    def _redact_text(self, text: str) -> tuple[str, int]:
        result = text
        count = 0
        for index, pattern in enumerate(self._compiled_patterns):
            if index == 0:
                result, changed = pattern.subn(lambda match: f"{match.group(1)}=[REDACTED]", result)
            elif index == 1:
                result, changed = pattern.subn("Bearer [REDACTED]", result)
            elif index == 2:
                result, changed = pattern.subn("[REDACTED_PRIVATE_KEY]", result)
            else:
                result, changed = pattern.subn("[REDACTED]", result)
            count += changed
        return result, count

    def _sanitize_artifacts(self, artifacts: Iterable[Any]) -> list[dict[str, Any]]:
        if self.artifact_mode == "drop":
            return []
        output: list[dict[str, Any]] = []
        allowed_fields = {"kind", "path", "exists", "size", "sha256"}
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            artifact = dict(item) if self.artifact_mode == "preserve" else {
                key: item[key] for key in allowed_fields if key in item
            }
            size = artifact.get("size")
            if isinstance(size, int) and size > self.artifact_max_size:
                artifact["policy_status"] = "oversize_reference"
            output.append(artifact)
        return output


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    normalized = value.replace("\\", "/").lower()
    return any(fnmatch.fnmatch(normalized, pattern.replace("\\", "/").lower()) for pattern in patterns)


def _source_path(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return str(payload.get("transcript") or payload.get("path") or event.get("cwd") or "")
