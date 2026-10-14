"""Archive real evaluation evidence and report the fixed 11-task, five-trial batch.

This module never runs an environment or a model. Missing evidence remains missing;
the synthetic records in its tests must never be used as evaluation results.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


TASKS = (
    "click_mouse", "fold_glasses", "hammer_nail", "pick_bucket", "pinch_tongs",
    "water_plant", "bimanual_assembly", "bimanual_hanoi",
    "bimanual_microwave_cook", "bimanual_photograph", "bimanual_unlock_ipad",
)
TRIALS_PER_TASK = 5
REDACTED = "<redacted>"
LIMITATIONS = [
    "Only exportable public model inputs, outputs and tool records are archived. "
    "Private/internal model reasoning is unavailable; this is not a complete model conversation.",
    "The requested model and settings can be recorded, but the backend model version "
    "has not been independently verified.",
    "The archive can verify recorded commands and declared recorder counts; it cannot "
    "independently prove that a command was never omitted by the recorder.",
]
_GENERATED = {
    "audit.json", "evidence_inventory.json", "missing_evidence.json", "attempt.json",
    "source_path_mapping.json", "redaction_manifest.json", "command_errors.json",
}
_SECRET_KEYS = {
    "token", "preview_token", "api_key", "apikey", "access_token", "refresh_token",
    "authorization", "password", "client_secret", "app_secret", "secret",
}
_SECRET_KEY_PATTERN = "|".join(sorted(_SECRET_KEYS, key=len, reverse=True))
_QUERY_SECRET = re.compile(
    r"(?P<prefix>[?&](?:token|preview_token|api_key|access_token)=)"
    r"(?P<secret>[^\s&#\"'\\<>]+)", re.IGNORECASE,
)
_FIELD_SECRET = re.compile(
    r'(?P<prefix>[\"\'](?:' + _SECRET_KEY_PATTERN + r')[\"\']\s*:\s*)'
    r'(?P<quote>[\"\'])(?P<secret>(?:\\.|(?!(?P=quote))[^\r\n])*)(?P=quote)', re.IGNORECASE,
)
_BEARER = re.compile(r"(?P<prefix>\bBearer\s+)(?P<secret>[A-Za-z0-9._~+/-]+=*)", re.IGNORECASE)
_EPISODE_COLUMNS = (
    "batch_id", "task", "trial_index", "attempt_id", "seed", "label", "scored",
    "status", "succeed", "terminated", "truncated", "steps", "action_calls",
    "reason", "evidence_path", "exception_class", "replacement_of",
    "evidence_complete", "audit_status",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _key(value: str) -> str:
    return value.lower().replace("-", "_")


class _Redactor:
    """Preserve bytes unless a secret is present; do not retain original secrets."""

    def __init__(self, explicit_values: list[str] | None = None):
        self.secrets = {value for value in (explicit_values or []) if value and value != REDACTED}

    def discover(self, text: str) -> None:
        for pattern in (_QUERY_SECRET, _FIELD_SECRET, _BEARER):
            for match in pattern.finditer(text):
                value = match.group("secret")
                if len(value) >= 8 and value != REDACTED:
                    self.secrets.add(value)

    def text(self, value: str) -> tuple[str, int]:
        count = 0

        def replace(match: re.Match) -> str:
            nonlocal count
            if match.group("secret") == REDACTED:
                return match.group(0)
            count += 1
            quote = match.groupdict().get("quote", "")
            return match.group("prefix") + quote + REDACTED + quote

        for pattern in (_QUERY_SECRET, _FIELD_SECRET, _BEARER):
            value = pattern.sub(replace, value)
        for secret in sorted(self.secrets, key=len, reverse=True):
            occurrences = value.count(secret)
            if occurrences:
                count += occurrences
                value = value.replace(secret, REDACTED)
        return value, count

    def object(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                name: REDACTED if _key(str(name)) in _SECRET_KEYS and item is not None
                else self.object(item)
                for name, item in value.items()
            }
        if isinstance(value, list):
            return [self.object(item) for item in value]
        return self.text(value)[0] if isinstance(value, str) else value


def _excluded(path: Path) -> str | None:
    name = path.name.lower()
    if path.is_symlink():
        return "symlink is not followed"
    if name in {".git", ".venv", "venv", "__pycache__", ".cache", "node_modules",
                "privat", ".ssh", ".aws", "credentials", "secrets"}:
        return "credentials, environment or cache directory"
    if (name in {".env", ".envrc"} or name.startswith(".env.") or name.endswith((".pid", ".sock", ".socket", ".pyc", ".pem", ".key"))
            or re.search(r"(^|[._-])(credentials?|secrets?|api[_-]?key|access[_-]?token|preview[_-]?token)([._-]|$)", name)
            or name in {"mine.md", "id_rsa", "id_ed25519", "credentials.json", "credentials.yaml",
                        "secrets.json", "secrets.yaml", "token.json", "preview-token", "preview-token.txt"}):
        return "credential, environment, PID or socket file"
    mode = path.lstat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        return "non-regular file (socket, pipe or device)"
    return None


def _inventory(directory: Path) -> dict:
    files = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if relative == "evidence_inventory.json":
            continue
        if path.is_symlink():
            raise ValueError(f"Evidence inventory refuses symlink: {relative}")
        if path.is_file():
            files.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": _sha256(path)})
    return {
        "schema_version": 1, "algorithm": "sha256", "files": files,
        "excluded_from_hashing": ["evidence_inventory.json"],
        "exclusion_reason": "A checksum manifest cannot include its own final checksum. Nested manifests are included.",
    }


def verify_inventory(directory: Path) -> dict:
    """Verify every file and detect both missing files and unlisted additions."""
    directory = Path(directory)
    try:
        saved = json.loads((directory / "evidence_inventory.json").read_text(encoding="utf-8"))
        current = _inventory(directory)
        if (not isinstance(saved, dict) or not isinstance(saved.get("files"), list) or
                any(not isinstance(item, dict) or set(item) != {"path", "size_bytes", "sha256"}
                    or not isinstance(item["path"], str) for item in saved["files"])):
            raise ValueError("Unsupported inventory schema")
    except (OSError, ValueError, TypeError) as error:
        return {"passed": False, "errors": [str(error)]}
    expected = {item["path"]: item for item in saved.get("files", [])}
    actual = {item["path"]: item for item in current["files"]}
    errors = []
    for path in sorted(expected.keys() | actual.keys()):
        if path not in expected:
            errors.append(f"Unlisted file: {path}")
        elif path not in actual:
            errors.append(f"Missing file: {path}")
        elif expected[path] != actual[path]:
            errors.append(f"Size or SHA-256 mismatch: {path}")
    if len(expected) != len(saved.get("files", [])):
        errors.append("Duplicate inventory paths")
    return {"passed": not errors, "file_count": len(actual), "errors": errors}


class _Audit:
    def __init__(self, root: Path, metadata: dict, mapping: dict[str, str]):
        self.root, self.metadata, self.mapping = root, metadata, mapping
        self.checks: list[dict] = []
        self.missing: list[dict] = []
        self.references: list[dict] = []
        self.errors: list[dict] = []
        self.counts: dict[str, Any] = {}
        self.record_lines: dict[str, list[int]] = {}

    def check(self, name: str, passed: bool, **details: Any) -> None:
        self.checks.append({"name": name, "status": "passed" if passed else "failed", **details})

    def gap(self, item: str, reason: str) -> None:
        entry = {"item": item, "reason": reason, "stage": self.metadata.get("phase", "archive"),
                 "exception_class": self.metadata.get("exception_class")}
        if entry not in self.missing:
            self.missing.append(entry)
            self.checks.append({"name": item, "status": "missing", "reason": reason})

    def read(self, relative: str, *, jsonl: bool = False, required: bool = True) -> Any:
        path = self.root / relative
        if not path.is_file():
            if required:
                self.gap(relative, "File was not generated or could not be collected; no replacement was fabricated.")
            return None
        try:
            if not jsonl:
                return json.loads(path.read_text(encoding="utf-8"))
            records = []
            self.record_lines[relative] = []
            with path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, 1):
                    if line.strip():
                        try:
                            records.append(json.loads(line))
                            self.record_lines[relative].append(line_number)
                        except (ValueError, TypeError) as error:
                            self.gap(f"{relative}:{line_number}", f"Invalid JSON record: {error}")
            return records
        except (OSError, ValueError, TypeError) as error:
            self.gap(relative, f"Cannot parse evidence: {error}")
            return None

    def field(self, record: dict, field: str, source: str, expected_type: type) -> Any:
        value = record.get(field)
        if type(value) is not expected_type:
            self.gap(f"{source}:{field}", f"Missing or unsupported field; expected {expected_type.__name__}.")
            return None
        return value

    def reference(self, original: Any, source: str, *, directory: bool = False) -> str | None:
        relative = self.mapping.get(str(original))
        if relative is None and isinstance(original, str) and not Path(original).is_absolute():
            candidate = (self.root / original).resolve()
            if candidate.is_relative_to(self.root.resolve()):
                relative = candidate.relative_to(self.root.resolve()).as_posix()
        path = self.root / relative if relative is not None else None
        exists = path is not None and (path.is_dir() if directory else path.is_file())
        self.references.append({"referenced_by": source, "original_path": original,
                                "archived_path": relative, "exists": exists})
        if not exists:
            self.gap(source, f"Referenced {'directory' if directory else 'file'} was not archived: {original}")
            return None
        if not directory and path.stat().st_size == 0:
            self.check(source + ":nonempty", False, path=relative)
        return relative


def _dict_record(audit: _Audit, value: Any, path: str) -> dict:
    if isinstance(value, dict):
        return value
    if value is not None:
        audit.gap(path + ":schema", "Expected a JSON object; unsupported schema is retained unchanged.")
    return {}


def _audit_model_images(audit: _Audit, transcript: list) -> None:
    """Validate each distinct externalized image without loading model history."""
    unique: set[str] = set()
    digests: dict[Path, str] = {}
    reference_count = 0

    def digest(path: Path) -> str:
        if path not in digests:
            digests[path] = _sha256(path)
        return digests[path]

    def local_file(value: Any, tag: str) -> Path | None:
        safe = (isinstance(value, str) and bool(value) and "\\" not in value
                and not Path(value).is_absolute() and ".." not in Path(value).parts
                and (audit.root / value).resolve().is_relative_to(audit.root.resolve()))
        audit.check(tag + ":path_safe", safe, actual=value)
        if not safe:
            audit.gap(tag, "Image reference must be a safe relative path inside this archive.")
            return None
        relative = audit.reference(value, tag)
        return audit.root / relative if relative is not None else None

    def walk(value: Any, location: str) -> None:
        nonlocal reference_count
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{location}/{index}")
        elif isinstance(value, dict):
            if "externalized_data_url" in value:
                reference_count += 1
                signature = json.dumps(value, sort_keys=True, ensure_ascii=False)
                if signature in unique:
                    return
                unique.add(signature)
                audit.check(location + ":externalization_marker", value["externalized_data_url"] is True)
                image_path = local_file(value.get("path"), location + ":image")
                source_path = local_file(value.get("source_archive_path"), location + ":source_image")
                expected_sha = value.get("sha256")
                valid_sha = isinstance(expected_sha, str) and bool(re.fullmatch(r"[0-9a-f]{64}", expected_sha))
                audit.check(location + ":sha256_format", valid_sha)
                mime = value.get("mime_type")
                audit.check(location + ":declared_mime", mime == "image/jpeg", actual=mime, expected="image/jpeg")
                if image_path is not None:
                    # JPEG is the frozen image strategy. Check the byte signature
                    # as well as MIME/extension; hashing ties it to the original
                    # bridge-produced contact sheet, whose bytes are retained.
                    with image_path.open("rb") as image_file:
                        header = image_file.read(3)
                    audit.check(location + ":mime_matches_file", mime == "image/jpeg"
                                and image_path.suffix.lower() in {".jpg", ".jpeg"}
                                and header == b"\xff\xd8\xff")
                    audit.check(location + ":image_sha256", valid_sha and digest(image_path) == expected_sha,
                                actual=digest(image_path), expected=expected_sha)
                if source_path is not None:
                    source_relative = source_path.relative_to(audit.root)
                    source_is_observation = (len(source_relative.parts) == 2
                        and bool(re.fullmatch(r"obs-\d+", source_relative.parts[0]))
                        and source_relative.name == "contact_sheet.jpg")
                    audit.check(location + ":source_is_contact_sheet", source_is_observation,
                                actual=source_relative.as_posix())
                    audit.check(location + ":source_observation", value.get("source_observation") == source_relative.parent.name,
                                actual=value.get("source_observation"), expected=source_relative.parent.name)
                    audit.check(location + ":source_sha256", valid_sha and digest(source_path) == expected_sha,
                                actual=digest(source_path), expected=expected_sha)
                if image_path is not None and source_path is not None:
                    audit.check(location + ":identical_to_original_contact_sheet", digest(image_path) == digest(source_path))
                return
            for key, child in value.items():
                walk(child, location + "/" + str(key))

    for index, record in enumerate(transcript, 1):
        walk(record, f"model_transcript.jsonl:{index}")
    audit.counts["model_image_references"] = reference_count
    audit.counts["unique_model_image_references"] = len(unique)


def _audit_archive(audit: _Audit) -> tuple[dict, dict]:
    manifest = _dict_record(audit, audit.read("manifest.json"), "manifest.json")
    summary = _dict_record(audit, audit.read("summary.json"), "summary.json")
    dispatch = _dict_record(audit, audit.read("dispatch.json"), "dispatch.json")
    events = audit.read("events.jsonl", jsonl=True)
    commands = audit.read("command_log.jsonl", jsonl=True)
    if not (audit.root / "runner-log.txt").is_file():
        audit.gap("runner-log.txt", "Dedicated runner stdout/stderr was not collected.")
    known_json = {"manifest.json", "summary.json", "dispatch.json", "events.jsonl", "command_log.jsonl"}
    for path in sorted(audit.root.rglob("*")):
        relative = path.relative_to(audit.root).as_posix()
        if path.is_file() and relative not in known_json and path.suffix in {".json", ".jsonl"}:
            audit.read(relative, jsonl=path.suffix == ".jsonl")

    for field in ("batch_id", "task", "trial_index", "attempt_id", "seed", "label", "scored", "status"):
        if field not in audit.metadata:
            audit.gap("metadata:" + field, "Required external evaluation metadata is absent.")
    task = audit.metadata.get("task")
    audit.check("task_in_fixed_eleven", task in TASKS, actual=task)
    audit.check("seed_zero", type(audit.metadata.get("seed")) is int and audit.metadata["seed"] == 0, actual=audit.metadata.get("seed"))
    trial = audit.metadata.get("trial_index")
    audit.check("trial_index_valid", type(trial) is int and 1 <= trial <= TRIALS_PER_TASK, actual=trial)
    audit.check("scored_is_boolean", type(audit.metadata.get("scored")) is bool)
    dim = 46 if str(task).startswith("bimanual_") else 23
    for source, record in (("manifest.json", manifest), ("summary.json", summary)):
        for field in ("task", "seed", "label"):
            if field not in record:
                audit.gap(source + ":" + field, "Identity field is absent.")
            else:
                audit.check(source + ":" + field, record[field] == audit.metadata.get(field), actual=record[field], expected=audit.metadata.get(field))
        if "native_action_dim" in record:
            audit.check(source + ":native_action_dim", record["native_action_dim"] == dim, actual=record["native_action_dim"], expected=dim)

    if events is None:
        events = []
    if not isinstance(events, list):
        audit.gap("events.jsonl:schema", "Expected JSONL records.")
        events = []
    for index, event in enumerate(events, 1):
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            audit.gap(f"events.jsonl:{index}:schema", "Unknown event schema is retained, but cannot be checked.")
    event_records = [event for event in events if isinstance(event, dict)]
    steps = [event for event in event_records if event.get("event") == "step"]
    requests = [event for event in event_records if event.get("event") == "agent_request"]
    resets = [event for event in event_records if event.get("event") == "reset"]
    observations = [event for event in event_records if event.get("event") == "observation"]
    audit.counts.update(steps=len(steps), action_calls=len(requests), reset_count=len(resets), observation_events=len(observations))
    audit.check("one_reset_per_episode", len(resets) == audit.metadata.get("expected_reset_count", 1), actual=len(resets), expected=audit.metadata.get("expected_reset_count", 1))
    audit.check("consecutive_step_numbers", all(type(event.get("step")) is int for event in steps)
                and [event.get("step") for event in steps] == list(range(1, len(steps) + 1)),
                actual=[event.get("step") for event in steps])
    for field, count in (("steps", len(steps)), ("action_calls", len(requests))):
        value = audit.field(summary, field, "summary.json", int)
        if value is not None:
            audit.check("summary_" + field, value == count, actual=value, events=count)
    if "reset_count" in summary:
        audit.check("summary_reset_count", summary["reset_count"] == len(resets), actual=summary["reset_count"], events=len(resets))
    official_success = False
    last_terminated = last_truncated = False
    for index, event in enumerate(steps, 1):
        for field in ("succeed", "terminated", "truncated"):
            value = audit.field(event, field, f"step:{index}", bool)
            if field == "succeed" and value is True:
                official_success = True
        if index > 1:
            audit.check(f"step:{index}:after_terminal", not (last_terminated or last_truncated))
        last_terminated, last_truncated = event.get("terminated"), event.get("truncated")
        state = audit.field(event, "robot_state", f"step:{index}", list)
        if state is not None:
            audit.check(f"step:{index}:robot_state_dimensions", len(state) == dim, actual=len(state), expected=dim)
    for field, expected in (("succeed", official_success), ("terminated", last_terminated), ("truncated", last_truncated)):
        value = audit.field(summary, field, "summary.json", bool)
        if value is not None:
            audit.check("summary_" + field, value == expected, actual=value, events=expected)
    audit.counts["official_success_ever_true"] = official_success

    expected_cameras = (["ego", "wrist_left", "wrist_right"] if dim == 46 else
                        ["ego_right", "wrist"] if task == "click_mouse" else ["front", "wrist"])
    cameras = manifest.get("camera_keys")
    if not isinstance(cameras, list) or any(not isinstance(camera, str) for camera in cameras):
        audit.gap("manifest.json:camera_keys", "Selected camera names are unavailable.")
        cameras = expected_cameras
    audit.check("official_selected_cameras", set(cameras) == set(expected_cameras), actual=cameras, expected=expected_cameras)
    observation_dirs = sorted(path for path in audit.root.glob("obs-*") if path.is_dir())
    if not observation_dirs:
        audit.gap("obs-*", "No initial or subsequent observation directories were collected.")
    event_observation_dirs = []
    for index, event in enumerate(observations, 1):
        relative = audit.reference(event.get("path"), f"observation_event:{index}", directory=True)
        if relative is not None:
            event_observation_dirs.append(relative)
    actual_observation_dirs = [path.relative_to(audit.root).as_posix() for path in observation_dirs]
    audit.check("observation_directories_match_events", sorted(event_observation_dirs) == actual_observation_dirs,
                events=len(event_observation_dirs), directories=len(actual_observation_dirs))
    audit.counts["observation_directories"] = len(observation_dirs)
    for directory in observation_dirs:
        relative = directory.relative_to(audit.root).as_posix() + "/observation.json"
        observation = _dict_record(audit, audit.read(relative), relative)
        state = audit.field(observation, "robot_state", relative, list)
        if state is not None:
            audit.check(relative + ":robot_state_dimensions", len(state) == dim, actual=len(state), expected=dim)
        images = observation.get("images")
        if not isinstance(images, dict):
            audit.gap(relative + ":images", "Camera-to-image mapping is absent or unsupported.")
            images = {}
        audit.check(relative + ":selected_cameras", set(images) == set(cameras), actual=list(images), expected=cameras)
        for camera in cameras:
            audit.reference(images.get(camera), relative + ":images:" + str(camera))
        audit.reference(observation.get("contact_sheet"), relative + ":contact_sheet")
        for field in ("step_count", "action_calls"):
            value = audit.field(observation, field, relative, int)
            maximum = len(steps) if field == "step_count" else len(requests)
            if value is not None:
                audit.check(relative + ":" + field, 0 <= value <= maximum, actual=value, maximum=maximum)
    if observation_dirs:
        initial_path = observation_dirs[0].relative_to(audit.root).as_posix() + "/observation.json"
        initial = _dict_record(audit, audit.read(initial_path), initial_path)
        audit.check("initial_observation_step_zero", initial.get("step_count") == 0, actual=initial.get("step_count"))

    commands = commands if isinstance(commands, list) else []
    action_commands = 0
    all_action_commands = 0
    command_lines = audit.record_lines.get("command_log.jsonl", list(range(1, len(commands) + 1)))
    for index, command in zip(command_lines, commands):
        if not isinstance(command, dict):
            audit.gap(f"command_log.jsonl:{index}:schema", "Expected a command record object.")
            continue
        request = command.get("request")
        if request is None:
            audit.gap(f"command_log.jsonl:{index}:request", "Actual request was not recorded.")
        if not any(key in command for key in ("response", "stdout", "exception", "error")):
            audit.gap(f"command_log.jsonl:{index}:response", "Actual response or exception was not recorded.")
        rc = command.get("returncode", command.get("rc"))
        if type(rc) is not int and not (rc is None and command.get("exception")):
            audit.gap(f"command_log.jsonl:{index}:returncode", "Actual exit/result code was not recorded.")
        if not ("timestamp" in command or "time" in command or ("started_at" in command and "ended_at" in command)):
            audit.gap(f"command_log.jsonl:{index}:time", "Command timestamp or start/end times were not recorded.")
        response = command.get("response")
        error = ((rc is not None and rc != 0) or (isinstance(response, dict) and response.get("ok") is False)
                 or bool(command.get("exception")) or bool(command.get("error")))
        if error:
            audit.errors.append({"command_log_line": index, "returncode": rc,
                                 "response_ok": response.get("ok") if isinstance(response, dict) else None,
                                 "exception_class": command.get("exception_class"),
                                 "evidence_path": "command_log.jsonl"})
        if isinstance(request, dict) and request.get("op") in ("move", "hold", "act"):
            all_action_commands += 1
            if not error:
                action_commands += 1
    audit.counts.update(command_count=len(commands), command_error_count=len(audit.errors), successful_action_command_count=action_commands)
    if not commands:
        audit.gap("command_log.jsonl:records", "No recorded attempt lifecycle or tool commands.")
    # Failed commands can enter agent_request before an execution exception, so
    # successful commands are a lower bound, not the complete action-call count.
    audit.check("successful_commands_have_agent_requests", action_commands <= len(requests), commands=action_commands, events=len(requests))
    audit.check("agent_requests_have_command_records", len(requests) <= all_action_commands, events=len(requests), commands=all_action_commands)
    for name in ("command_count", "command_error_count"):
        if name in audit.metadata:
            audit.check(name + "_matches_recorder", audit.metadata[name] == audit.counts[name], expected=audit.metadata[name], actual=audit.counts[name])

    isolation = audit.metadata.get("isolation", dispatch.get("isolation"))
    if not isinstance(isolation, dict):
        audit.gap("isolation_verification", "External context/file-access isolation verification is absent.")
    else:
        audit.check("isolation_verified", isolation.get("verified") is True, method=isolation.get("method"))
    initial_check = audit.metadata.get("initial_state_verification")
    if initial_check is None:
        initial_check = audit.read("initial_state_verification.json")
    if isinstance(initial_check, dict):
        audit.check("initial_state_verified", initial_check.get("passed") is True, verification=initial_check)
    elif initial_check is not None:
        audit.gap("initial_state_verification:schema", "Expected an external verification object.")
    requirements = audit.metadata.get("evidence_requirements", [])
    for relative in requirements:
        candidate = (audit.root / relative).resolve()
        if not candidate.is_relative_to(audit.root.resolve()) or not candidate.is_file():
            audit.gap(str(relative), "Required evidence declared by the scheduler is absent.")
    transcript = audit.read("model_transcript.jsonl", jsonl=True, required=False)
    _audit_model_images(audit, transcript or [])
    model_events = {row.get("event") for row in (transcript or [])
                    if isinstance(row, dict) and isinstance(row.get("event"), str)}
    audit.counts["public_model_error_records"] = sum(isinstance(row, dict) and row.get("event") in
        ("model_error", "tool_rejected", "infrastructure_error") for row in (transcript or []))
    if audit.metadata.get("phase") not in {"startup", "initialization", "initial_verification"}:
        for kind in ("request", "response"):
            found = "model_" + kind in model_events or any(
                path.is_file() and ("model_" + kind) in path.name.lower()
                for path in audit.root.rglob("*")
            )
            if not found:
                audit.gap("public_model_" + kind, "Exportable public model " + kind + " evidence was not collected or has an unknown schema.")

    failed = [item for item in audit.checks if item["status"] == "failed"]
    status = "failed" if failed else "incomplete" if audit.missing else "passed"
    result = {"schema_version": 1, "status": status, "passed": status == "passed",
              "evidence_complete": status == "passed", "checks": audit.checks, "counts": audit.counts,
              "missing_evidence_count": len(audit.missing), "failed_check_count": len(failed),
              "platform_limitations": LIMITATIONS,
              "manifest_interpretation": "Bridge manifest model_api_used/purpose fields describe the bridge defaults. "
              "The external dispatch, batch configuration and public model records document the actual evaluation; original manifest fields are preserved."}
    return result, summary


def archive_attempt(*, episode_dir: Path | None, attempt_dir: Path, destination: Path, metadata: dict) -> dict:
    """Copy one attempt, redact secrets, audit actual records and inventory all files.

    Required metadata: batch_id, task, trial_index (1..5), attempt_id, seed,
    label, scored (bool), status. Prefer status='completed' or 'interrupted'.
    Optional provenance: reason, exception_class, replacement_of, phase, isolation
    {method, verified}, initial_state_verification {passed, ...}, model, source_commit,
    frozen_config_sha256, expected_reset_count, command_count, command_error_count,
    evidence_requirements (relative filenames). Private _redaction_values are consumed
    in memory and never archived. Result fields come from the actual summary only.

    Existing nonempty destinations are refused. The source directories are never
    changed. Every retained source file has an explicit original/archive mapping;
    conflicting attempt files are retained under attempt-files/.
    """
    attempt_dir, destination = Path(attempt_dir).resolve(), Path(destination).resolve()
    episode_dir = Path(episode_dir).resolve() if episode_dir is not None else None
    for source in (episode_dir, attempt_dir):
        if source is not None and (destination == source or destination.is_relative_to(source)):
            raise ValueError("Archive destination must be outside source evidence directories")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Refusing to overwrite an archived attempt: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    metadata = dict(metadata)
    explicit_values = metadata.pop("_redaction_values", [])
    redactor = _Redactor(explicit_values)
    redactor.discover(json.dumps(metadata, ensure_ascii=False))
    sources: list[tuple[str, Path, Path]] = []
    excluded, collection_gaps = [], []
    mapping: dict[str, str] = {}
    mapping_records: list[dict] = []
    for kind, root in (("episode", episode_dir), ("attempt", attempt_dir)):
        if root is None or not root.is_dir():
            collection_gaps.append({"item": kind + "_dir", "reason": "Source directory does not exist or was never created."})
            continue
        mapping[str(root)] = "."
        for directory, dirs, filenames in os.walk(root, followlinks=False):
            dirs.sort()
            filenames.sort()
            current = Path(directory)
            for name in list(dirs):
                path = current / name
                reason = _excluded(path)
                if reason:
                    excluded.append({"source_path": str(path), "reason": reason})
                    dirs.remove(name)
                else:
                    mapping[str(path)] = path.relative_to(root).as_posix()
            for name in filenames:
                source = current / name
                relative = source.relative_to(root)
                reason = _excluded(source)
                if reason:
                    excluded.append({"source_path": str(source), "reason": reason})
                    continue
                sources.append((kind, source, relative))
                try:
                    redactor.discover(source.read_text(encoding="utf-8"))
                except UnicodeError:
                    pass  # Binary image/asset; preserve it byte-for-byte.
                except OSError as error:
                    collection_gaps.append({"item": str(source), "reason": str(error)})
    redactions = []
    for kind, source, relative in sources:
        if relative.as_posix() in _GENERATED:
            relative = Path("source-files") / kind / relative
        target = destination / relative
        try:
            original = source.read_bytes()
            count = 0
            try:
                text, count = redactor.text(original.decode("utf-8"))
                content = text.encode("utf-8")
            except UnicodeError:
                content = original
            if target.exists() and target.read_bytes() != content:
                relative = Path("attempt-files") / relative
                target = destination / relative
                if target.exists():
                    raise FileExistsError(f"Evidence path collision: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            archived_path = relative.as_posix()
            mapping[str(source)] = archived_path
            mapping_records.append({"source_kind": kind, "original_path": str(source), "archived_path": archived_path,
                                    "source_sha256": hashlib.sha256(original).hexdigest(),
                                    "archived_sha256": hashlib.sha256(content).hexdigest(), "redacted": bool(count)})
            if count:
                redactions.append({"path": archived_path, "replacement_count": count, "placeholder": REDACTED,
                                   "reason": "Credential/authorization value or preview URL token; other evidence retained."})
        except OSError as error:
            collection_gaps.append({"item": str(source), "reason": str(error)})
    metadata = redactor.object(metadata)
    audit = _Audit(destination, metadata, mapping)
    for gap in collection_gaps:
        audit.gap(gap["item"], gap["reason"])
    for item in excluded:
        if item["reason"] == "symlink is not followed":
            audit.gap(item["source_path"], "Symlink evidence was excluded; no external target was followed.")
    audit_result, summary = _audit_archive(audit)
    record = {key: metadata.get(key) for key in _EPISODE_COLUMNS}
    record.update({key: summary.get(key) for key in ("succeed", "terminated", "truncated", "steps", "action_calls")})
    record["reason"] = metadata.get("reason") or summary.get("reason")
    record["evidence_path"] = metadata.get("evidence_path") or f"episodes/{metadata.get('task')}/{metadata.get('attempt_id')}"
    record.update(evidence_complete=audit_result["evidence_complete"], audit_status=audit_result["status"], audit_counts=audit.counts)
    # Interrupted attempts remain visible and cannot accidentally count as failures.
    if record["status"] == "interrupted":
        record["scored"] = False
    _write_json(destination / "attempt.json", {**metadata, **record})
    _write_json(destination / "command_errors.json", {"errors": audit.errors, "count": len(audit.errors),
                                                     "source": "All command_log.jsonl records; no error records are deleted."})
    _write_json(destination / "audit.json", audit_result)
    if audit.missing:
        _write_json(destination / "missing_evidence.json", {"missing": audit.missing, "phase": metadata.get("phase"),
                                                            "exception_class": metadata.get("exception_class"),
                                                            "reason": metadata.get("reason")})
    _write_json(destination / "source_path_mapping.json", {"files": mapping_records,
                "directories": [{"original_path": source, "archived_path": target} for source, target in sorted(mapping.items())
                                if source not in {item["original_path"] for item in mapping_records}],
                "referenced_paths": audit.references,
                "policy": "Original JSON paths are preserved; this mapping resolves them to committed archive files."})
    _write_json(destination / "redaction_manifest.json", {"placeholder": REDACTED, "redactions": redactions,
                "excluded_files": excluded, "original_secret_values_retained": False})
    _write_json(destination / "evidence_inventory.json", _inventory(destination))
    verified = verify_inventory(destination)
    if not verified["passed"]:
        raise RuntimeError("New archive failed inventory verification: " + "; ".join(verified["errors"]))
    return record


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: (str(value).lower() if isinstance(value, bool) else value)
                             for key, value in row.items() if key in columns})


def _markdown(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def update_batch(batch_dir: Path, batch_config: dict, attempts: list[dict]) -> None:
    """Write all 11 task rows and all attempts; publish rates only at exactly 5/55.

    Each result row must come from archive_attempt. Repeated completed trial indexes
    invalidate formal rates instead of selecting the best attempt. Evidence status
    is separate from task score. Existing batch_config.json is frozen and cannot
    change silently through this function.
    """
    batch_dir = Path(batch_dir).resolve()
    batch_dir.mkdir(parents=True, exist_ok=True)
    config_path = batch_dir / "batch_config.json"
    if config_path.exists():
        if json.loads(config_path.read_text(encoding="utf-8")) != batch_config:
            raise ValueError("batch_config.json is frozen; changed configuration requires a new batch")
    else:
        _write_json(config_path, batch_config)
    ids = [(row.get("task"), row.get("attempt_id")) for row in attempts]
    if len(ids) != len(set(ids)) or any(attempt_id is None for _, attempt_id in ids):
        raise ValueError("Every task/attempt_id pair must be distinct and non-null")
    if any(row.get("task") not in TASKS for row in attempts):
        raise ValueError("Attempt task is outside the fixed eleven-task batch")
    rows: list[dict] = []
    evidence_problems = []
    normalized_attempts = []
    for attempt in attempts:
        row = dict(attempt)
        if row.get("status") == "interrupted":
            row["scored"] = False
        relative = row.get("evidence_path")
        destination = (batch_dir / str(relative)).resolve() if relative else None
        if destination is None or not destination.is_relative_to(batch_dir) or not destination.is_dir():
            row["evidence_complete"] = False
            row["audit_status"] = "missing"
            evidence_problems.append(f"{row.get('attempt_id')}: archived evidence directory is absent")
        else:
            verification = verify_inventory(destination)
            if not verification["passed"]:
                row["evidence_complete"] = False
                row["audit_status"] = "failed"
                evidence_problems.extend(f"{row.get('attempt_id')}: {error}" for error in verification["errors"])
            try:
                archived_audit = json.loads((destination / "audit.json").read_text(encoding="utf-8"))
                archived_attempt = json.loads((destination / "attempt.json").read_text(encoding="utf-8"))
                for key in _EPISODE_COLUMNS:
                    if key not in {"audit_status", "evidence_complete"} and row.get(key) != archived_attempt.get(key):
                        raise ValueError(f"Result field {key} differs from archived attempt.json")
                if archived_audit.get("passed") is not True:
                    row["evidence_complete"] = False
                    row["audit_status"] = archived_audit.get("status", "incomplete")
                    evidence_problems.append(f"{row.get('task')}/{row.get('attempt_id')}: "
                        f"{archived_audit.get('missing_evidence_count', '?')} missing evidence items, "
                        f"{archived_audit.get('failed_check_count', '?')} failed audit checks; see {relative}/audit.json")
            except (OSError, ValueError, TypeError, AttributeError) as error:
                row["evidence_complete"] = False
                row["audit_status"] = "failed"
                evidence_problems.append(f"{row.get('task')}/{row.get('attempt_id')}: {error}")
        normalized_attempts.append(row)
    for task in TASKS:
        task_attempts = [row for row in normalized_attempts if row.get("task") == task]
        completed = [row for row in task_attempts if row.get("scored") is True and
                     row.get("status") in {"completed", "failed", "succeeded", "success", "failure"} and
                     type(row.get("succeed")) is bool]
        indexes = [row.get("trial_index") for row in completed]
        unique_valid = {index for index in indexes if type(index) is int and 1 <= index <= TRIALS_PER_TASK}
        unambiguous = len(indexes) == len(unique_valid)
        complete = len(completed) == TRIALS_PER_TASK and unique_valid == set(range(1, TRIALS_PER_TASK + 1)) and unambiguous
        successes = sum(row["succeed"] is True for row in completed)
        failures = len(completed) - successes
        interruption_count = sum(row.get("status") == "interrupted" for row in task_attempts)
        remaining = max(0, TRIALS_PER_TASK - len(unique_valid))
        reason = ("" if complete else "duplicate or invalid scored trial indices" if not unambiguous else
                  str(task_attempts[-1].get("reason") or "pending independent scored episodes") if task_attempts else "not started")
        rows.append({"task": task, "planned_scored_episodes": TRIALS_PER_TASK,
                     "completed_scored_episodes": len(completed), "successes": successes, "failures": failures,
                     "interrupted_attempts": interruption_count, "remaining_scored_episodes": remaining,
                     "task_success_rate": f"{successes / TRIALS_PER_TASK * 100:.0f}%" if complete else "",
                     "status": "complete" if complete else "未完成正式评测", "reason": reason,
                     "evidence_path": f"episodes/{task}",
                     "evidence_complete": all(row.get("evidence_complete") is True for row in task_attempts) if task_attempts else False})
    _write_csv(batch_dir / "episode_results.csv", _EPISODE_COLUMNS, normalized_attempts)
    columns = ("task", "planned_scored_episodes", "completed_scored_episodes", "successes", "failures",
               "interrupted_attempts", "remaining_scored_episodes", "task_success_rate", "status", "reason",
               "evidence_path", "evidence_complete")
    _write_csv(batch_dir / "task_results.csv", columns, rows)
    batch_id = batch_config.get("batch_id", batch_dir.name)
    total_completed = sum(row["completed_scored_episodes"] for row in rows)
    total_successes = sum(row["successes"] for row in rows)
    complete = all(row["status"] == "complete" for row in rows)
    evidence_complete = bool(attempts) and all(row.get("evidence_complete") is True for row in normalized_attempts) and not evidence_problems
    lines = [f"# Evaluation batch {_markdown(batch_id)}", "",
             "固定初始场景、环境 seed=0、每任务五轮、跨轮上下文隔离。五轮为小样本稳定性观察，不代表跨场景泛化或官方 benchmark 成绩。", "",
             f"计分进度：{total_completed}/55；成功 {total_successes} 轮；实际尝试 {len(attempts)} 次。",
             f"评测状态：{'全部 11 个任务已完成五轮' if complete else '未完成正式评测'}。证据状态：{'已归档并通过核验' if evidence_complete else '未全部通过完整证据核验'}。", ""]
    if complete:
        lines.extend([f"总体任务成功率：{total_successes}/55 = {total_successes / 55 * 100:.2f}%。", ""])
    else:
        lines.extend(["不足五轮的任务不发布正式任务成功率；全部 11 个任务完成前不发布正式总体成功率。", ""])
    lines.extend(["| task | 计划 | 有效完成 | 成功 | 失败 | 中断 | 缺少 | 任务成功率 | 状态 | 证据 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |"])
    for row in rows:
        evidence = f"[{row['task']}]({row['evidence_path']})" if any(a.get("task") == row["task"] for a in attempts) else "尚无"
        lines.append(f"| {row['task']} | 5 | {row['completed_scored_episodes']} | {row['successes']} | {row['failures']} | "
                     f"{row['interrupted_attempts']} | {row['remaining_scored_episodes']} | {row['task_success_rate'] or '未完成'} | {row['status']} | {evidence} |")
    lines.extend(["", "每次尝试（包括所有中断及补测）：", "",
                  "| task / trial | attempt | 计分 | 状态 | succeed | steps | action_calls | reason | 补测关联 | 核验 |",
                  "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |"])
    for row in normalized_attempts:
        lines.append(f"| {_markdown(row.get('task'))} / {_markdown(row.get('trial_index'))} | "
                     f"[{_markdown(row.get('attempt_id'))}]({_markdown(row.get('evidence_path'))}) | "
                     f"{str(row.get('scored')).lower()} | {_markdown(row.get('status'))} | {_markdown(row.get('succeed'))} | "
                     f"{_markdown(row.get('steps'))} | {_markdown(row.get('action_calls'))} | {_markdown(row.get('reason'))} | "
                     f"{_markdown(row.get('replacement_of'))} | {_markdown(row.get('audit_status'))} |")
    if any(row["status"] != "complete" for row in rows):
        lines.extend(["", "未完成任务及原因：", ""])
        lines.extend(f"- {row['task']}：缺少 {row['remaining_scored_episodes']} 轮；{_markdown(row['reason'])}。"
                     for row in rows if row["status"] != "complete")
    if evidence_problems:
        lines.extend(["", "归档核验问题：", ""])
        lines.extend("- " + _markdown(problem) for problem in evidence_problems)
    lines.extend(["", "可获取性与核验边界：", ""])
    lines.extend("- " + limitation for limitation in LIMITATIONS)
    lines.extend(["", "[批次配置](batch_config.json) · [逐任务 CSV](task_results.csv) · [逐尝试 CSV](episode_results.csv) · [全部证据 SHA-256 清单](evidence_inventory.json)", "",
                  "Git 提交与推送由外部调度者逐次执行；本报告生成本身不证明远程仓库已更新。", ""])
    (batch_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    _write_json(batch_dir / "evidence_inventory.json", _inventory(batch_dir))
