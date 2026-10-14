"""Stateless-per-episode GPT-6-Astra vision controller.

The model gets four fixed tools, filtered proprioception and the current
episode's camera contact sheets.  Files, the shell and lifecycle operations
are deliberately not model tools.  The caller owns runner lifecycle and
validates image access again in ``image_loader``.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterable
import urllib.error
import urllib.parse
import urllib.request


MODEL = "gpt-6-astra"
REASONING_EFFORT = "medium"
MAX_OUTPUT_TOKENS = 16384
RETRY_DELAYS = (2.0, 4.0, 8.0)
INITIAL_PROMPT = "请你读取CODEX_AGENT.md 这个教程 进行任务的测试"

POLICY = """You control one robot episode using the attached fixed guide and task
contract. Only official selected RGB contact sheets and robot proprioception
are available. A new contact sheet is provided after every tool result. Use
that new image before choosing the next short action. Keep and use the entire
current episode history. You have no access to files, shell, previous episodes,
object truth, resets, task selection or the environment lifecycle.

Choose at most one tool call per response: observe, move, hold, or act. Each
action may advance at most 30 environment steps, and may not exceed the
remaining native step limit. The simulator is paused while you reason.
Continue until official success, termination, truncation or the native step
limit; there is no additional action-count budget. Tool validation errors may
be corrected using the returned observation. If you choose to give up, provide
a final response with your reason and no tool call; it counts as a failed
episode. A final response without a tool call is always treated as giving up
when the environment has not already ended. Success is decided only by the
environment's official succeed flag, never by your final text.
"""

CONTRACT_FIELDS = frozenset({
    "task", "prompt", "prompt_source", "robot_type", "native_action_dim",
    "rotvec_action_dim", "native_action", "rotvec_action", "hand_order",
    "hand_joint_limits_rad", "position_bounds_m", "position_bounds_source",
    "move_arm_selection", "control_dt_seconds", "max_steps_per_request",
    "episode_step_limit", "observations", "camera_keys", "camera_profile",
    "camera_mapping_source", "camera_mapping", "camera_config_sha256",
    "success", "clock",
})
OBSERVATION_FIELDS = frozenset({
    "task", "prompt", "seed", "step_count", "action_calls", "sim_time",
    "robot_state", "tcp_pose_wxyz", "hand_joints", "right_tcp_pose_wxyz",
    "left_tcp_pose_wxyz", "right_hand_joints", "left_hand_joints",
    "last_target_23", "last_target_46", "succeed", "terminated", "truncated",
    "camera_keys", "camera_profile", "camera_mapping", "camera_config_sha256",
})


def _array(size: int) -> dict:
    return {"type": "array", "items": {"type": "number"},
            "minItems": size, "maxItems": size}


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _function(name: str, description: str, properties: dict) -> dict:
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False},
            "strict": True}


TOOLS = [
    _function("observe", "Get a fresh image and robot state without advancing time.", {}),
    _function("move", "Interpolate one arm's target over 1 to 30 steps. Positions are "
              "absolute world coordinates unless delta=true; quaternion order is wxyz. "
              "Use at most one of quat and rotvec. Null leaves that target unchanged. "
              "For a dual-arm robot arm is required; the other arm keeps its last target.", {
                  "xyz": _nullable(_array(3)), "quat": _nullable(_array(4)),
                  "rotvec": _nullable(_array(3)), "hand": _nullable(_array(16)),
                  "arm": _nullable({"type": "string", "enum": ["right", "left"]}),
                  "delta": {"type": "boolean"},
                  "steps": {"type": "integer", "minimum": 1, "maximum": 30},
                  "note": {"type": "string"},
              }),
    _function("hold", "Hold current legal targets for 1 to 30 environment steps.", {
        "steps": {"type": "integer", "minimum": 1, "maximum": 30},
        "note": {"type": "string"},
    }),
    _function("act", "Execute one 1 to 30 row action array using the current contract's "
              "native or rotvec ordering. This is one short action before a fresh image.", {
                  "actions": {"type": "array", "minItems": 1, "maxItems": 30,
                              "items": {"type": "array", "items": {"type": "number"}}},
                  "note": {"type": "string"},
              }),
]


class APIError(RuntimeError):
    """An error safe to record; response content is scrubbed before construction."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class InfrastructureError(RuntimeError):
    pass


def parse_response_stream(lines: Iterable[bytes], on_event: Callable[[dict], None] | None = None) -> dict:
    """Return only a complete Responses result; partial tool calls never escape.

    A caller can archive every decoded public event, including the partial
    events retained when a request later fails or loses its connection.
    """
    data_lines: list[str] = []

    def event_result() -> dict | None:
        if not data_lines:
            return None
        data = "\n".join(data_lines)
        data_lines.clear()
        if data == "[DONE]":
            return None
        try:
            event = json.loads(data)
        except ValueError:
            raise APIError("API returned invalid SSE event JSON", retryable=True) from None
        if not isinstance(event, dict):
            raise APIError("API returned a non-object SSE event", retryable=True)
        if on_event:
            on_event(event)
        kind = event.get("type")
        if kind == "response.completed":
            result = event.get("response")
            if not isinstance(result, dict) or result.get("status") != "completed":
                raise APIError("Completed SSE event has no completed response", retryable=True)
            return result
        if kind in ("error", "response.failed", "response.incomplete"):
            result = event.get("response", event)
            error = result.get("error") if isinstance(result, dict) else None
            error = error or event.get("error") or {}
            code = error.get("code") if isinstance(error, dict) else None
            raise APIError("API stream did not complete: " + json.dumps(event, ensure_ascii=False),
                           retryable=kind == "error" or code in ("server_error", "rate_limit_exceeded"))
        return None

    for raw_line in lines:
        try:
            line = raw_line.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise APIError("API returned invalid SSE encoding", retryable=True) from None
        if not line:
            result = event_result()
            if result is not None:
                return result
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    result = event_result()
    if result is not None:
        return result
    raise APIError("API stream ended before response.completed", retryable=True)


def load_api_key(path: Path) -> str:
    """Extract the user's single credential without exposing unrelated file content."""
    content = Path(path).read_text(encoding="utf-8")
    candidates = list(dict.fromkeys(re.findall(r"\bsk-[A-Za-z0-9_-]+", content)))
    if len(candidates) != 1:
        raise ValueError("Expected exactly one API credential in the specified file")
    return candidates[0]


def _finite_vector(value: Any, size: int, field: str) -> None:
    if (not isinstance(value, list) or len(value) != size
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise ValueError(f"{field} must contain exactly {size} finite numbers")


def validate_tool(payload: dict, contract: dict, remaining: int) -> dict:
    """Validate an operation before the caller can execute any side effect."""
    if not isinstance(payload, dict):
        raise ValueError("Tool arguments must be an object")
    op = payload.get("op")
    allowed = {
        "observe": {"op"},
        "move": {"op", "xyz", "quat", "rotvec", "hand", "arm", "delta", "steps", "note"},
        "hold": {"op", "steps", "note"},
        "act": {"op", "actions", "note"},
    }
    if op not in allowed:
        raise ValueError("Only observe, move, hold and act are allowed")
    if set(payload) - allowed[op]:
        raise ValueError("Unexpected tool argument; file and lifecycle access is not allowed")
    clean = {k: copy.deepcopy(v) for k, v in payload.items() if v is not None}
    if "note" in clean:
        note = clean["note"]
        if not isinstance(note, str) or len(note) > 200 or any(ord(c) < 32 for c in note):
            raise ValueError("note must be a single line of at most 200 characters")
    if op == "observe":
        return clean
    native_dim = contract.get("native_action_dim")
    rotvec_dim = contract.get("rotvec_action_dim")
    if native_dim not in (23, 46) or rotvec_dim != (22 if native_dim == 23 else 44):
        raise InfrastructureError("Unsupported robot action dimensions in contract")
    if op in ("move", "hold"):
        steps = clean.get("steps")
        if type(steps) is not int or not 1 <= steps <= min(30, remaining):
            raise ValueError("steps must be an integer within 1..min(30, remaining_steps)")
    if op == "move":
        if native_dim == 46 and clean.get("arm") not in ("right", "left"):
            raise ValueError("Dual-arm move requires arm=right or arm=left")
        if native_dim == 23 and "arm" in clean:
            raise ValueError("Single-arm move must omit arm or set it to null")
        if type(clean.get("delta", False)) is not bool:
            raise ValueError("delta must be a boolean")
        if "quat" in clean and "rotvec" in clean:
            raise ValueError("quat and rotvec are mutually exclusive")
        for key, size in (("xyz", 3), ("quat", 4), ("rotvec", 3), ("hand", 16)):
            if key in clean:
                _finite_vector(clean[key], size, key)
        if "quat" in clean and sum(x * x for x in clean["quat"]) < 1e-12:
            raise ValueError("quat must have a nonzero norm")
    if op == "act":
        actions = clean.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= min(30, remaining):
            raise ValueError("actions must have 1..min(30, remaining_steps) rows")
        first = actions[0]
        if not isinstance(first, list) or len(first) not in (native_dim, rotvec_dim):
            raise ValueError("Action row dimension must match the current task contract")
        for row in actions:
            _finite_vector(row, len(first), "action row")
    return clean


def _unwrap(observation: dict) -> dict:
    if not isinstance(observation, dict):
        raise InfrastructureError("Observation must be an object")
    if "ok" in observation:
        if observation["ok"] is not True or not isinstance(observation.get("result"), dict):
            raise InfrastructureError("Observation request failed")
        return observation["result"]
    return observation


def terminal_reason(observation: dict, step_limit: int) -> str | None:
    if observation.get("succeed") is True:
        return "official_success"
    if observation.get("step_count", 0) >= step_limit:
        return "native_step_limit"
    if observation.get("terminated") is True or observation.get("truncated") is True:
        return "official_failure"
    return None


class ModelRunner:
    """One client, with a brand-new local history in every ``run_episode`` call.

    ``transport`` is a test seam accepting an unsigned JSON request. A production
    client uses only the explicitly supplied ``base_url`` and the key from
    ``credentials_path``; it never falls back to an environment credential.
    """

    def __init__(self, credentials_path: Path, guide_text: str, *, base_url: str,
                 timeout: float = 180.0, transport: Callable[[dict], dict] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query
                or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("base_url must be an explicitly configured API origin/path")
        self._key = load_api_key(credentials_path)
        self._endpoint = base_url.rstrip("/") + "/responses"
        self._guide = guide_text
        self._timeout = timeout
        self._transport = transport or self._http_request
        self._sleep = sleep
        self._stream_event_sink: Callable[[dict], None] | None = None

    @staticmethod
    def policy_manifest() -> dict:
        return {"model": MODEL, "reasoning": {"effort": REASONING_EFFORT},
                "wire_api": "responses", "stream": True, "max_output_tokens": MAX_OUTPUT_TOKENS,
                "parallel_tool_calls": False, "store": False,
                "api_retry_delays_seconds": list(RETRY_DELAYS),
                "image_strategy": "one current official-camera contact_sheet JPEG per observation",
                "history_strategy": "full current-episode history; local list recreated each episode",
                "public_input_image_storage": "lossless data-URL externalization to model_images with SHA-256",
                "public_response_storage": "every decoded SSE event plus complete response; partial failed streams retained",
                "private_reasoning": "not available for export; only API-returned output is archived",
                "initial_user_prompt": INITIAL_PROMPT,
                "policy": POLICY, "tools": copy.deepcopy(TOOLS)}

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(self._key, "[REDACTED_API_KEY]")
        if isinstance(value, dict):
            return {str(k): self._scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v) for v in value]
        return value

    def _http_request(self, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self._endpoint, data=data, method="POST", headers={
            "Authorization": "Bearer " + self._key, "Content-Type": "application/json",
            "Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return parse_response_stream(response, self._stream_event_sink)
        except urllib.error.HTTPError as error:
            message = error.read().decode("utf-8", "replace")
            raise APIError(self._scrub(message), status=error.code,
                           retryable=error.code in (408, 409, 425, 429) or error.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise APIError(type(error).__name__, retryable=True) from None
        except APIError as error:
            raise APIError(self._scrub(str(error)), retryable=error.retryable, status=error.status) from None

    def run_episode(self, context: dict, contract: dict, initial_observation: dict,
                    image_loader: Callable[[str], dict], execute_tool: Callable[[dict], dict],
                    output_dir: Path, step_limit: int) -> dict:
        """Execute one episode; never start/reset/finish/stop the simulator.

        image_loader(path) returns {mime_type, data: bytes, archive_path?, sha256?}.
        execute_tool({op, ...}) returns {ok: bool, result: observation} or a
        validation error {ok: false, error: str}. Infrastructure failures should
        raise an exception or set infrastructure_error=true; actions are never
        automatically replayed. Returned status is completed or interrupted.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = output_dir / "model_transcript.jsonl"
        if transcript_path.exists():
            raise ValueError("Refusing to append a new episode to an existing model transcript")
        image_dir = output_dir / "model_images"
        image_dir.mkdir(exist_ok=True)
        observation = _unwrap(initial_observation)
        source_root = Path(observation.get("episode_dir", context.get("episode_dir", ""))).resolve()
        if not observation.get("episode_dir") and not context.get("episode_dir"):
            raise ValueError("Current episode_dir is required for access isolation")
        if type(step_limit) is not int or step_limit <= 0:
            raise ValueError("A positive native environment step limit is required")
        filtered_contract = {k: copy.deepcopy(v) for k, v in contract.items() if k in CONTRACT_FIELDS}
        if filtered_contract.get("native_action_dim") not in (23, 46):
            raise ValueError("Unknown robot dimensions")
        camera_keys = filtered_contract.get("camera_keys")
        if not isinstance(camera_keys, list) or len(camera_keys) not in (2, 3):
            raise ValueError("Contract must specify the official two or three camera keys")
        image_refs: dict[str, dict] = {}
        last_image_path: str | None = None
        seen_call_ids: set[str] = set()
        model_calls = 0
        accepted_action_calls = 0
        validation_errors = 0

        def externalize(value: Any) -> Any:
            if isinstance(value, str):
                return copy.deepcopy(image_refs[value]) if value in image_refs else self._scrub(value)
            if isinstance(value, list):
                return [externalize(v) for v in value]
            if isinstance(value, dict):
                return {k: externalize(v) for k, v in value.items()}
            return value

        def record(event: str, **values: Any) -> None:
            entry = {"event": event, "time_unix": time.time(), **values}
            with transcript_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(externalize(entry), ensure_ascii=False, allow_nan=False) + "\n")

        def public_observation(obs: dict) -> dict:
            if obs.get("task") != filtered_contract.get("task") or obs.get("seed") != context.get("seed", 0):
                raise InfrastructureError("Observation task or seed differs from this episode")
            if Path(obs.get("episode_dir", "")).resolve() != source_root:
                raise InfrastructureError("Observation belongs to a different episode")
            if obs.get("camera_keys") != camera_keys or set(obs.get("images", {})) != set(camera_keys):
                raise InfrastructureError("Observation does not use the frozen official cameras")
            _finite_vector(obs.get("robot_state"), filtered_contract["native_action_dim"], "robot_state")
            if type(obs.get("step_count")) is not int or obs["step_count"] < 0:
                raise InfrastructureError("Observation has invalid step_count")
            return {k: copy.deepcopy(v) for k, v in obs.items() if k in OBSERVATION_FIELDS}

        def image_message(obs: dict) -> dict:
            nonlocal last_image_path
            public = public_observation(obs)
            raw_path = obs.get("contact_sheet")
            if not isinstance(raw_path, str):
                raise InfrastructureError("Current observation has no contact_sheet")
            path = Path(raw_path).resolve()
            if not path.is_relative_to(source_root) or path.name != "contact_sheet.jpg":
                raise InfrastructureError("Contact sheet is outside the current episode")
            if path.parent.name[:4] != "obs-":
                raise InfrastructureError("Contact sheet must be in a current observation directory")
            if str(path) == last_image_path:
                raise InfrastructureError("A fresh observation is required after every tool result")
            for key in camera_keys:
                camera_path = Path(obs["images"][key]).resolve()
                if camera_path.parent != path.parent or camera_path.name != key + ".png":
                    raise InfrastructureError("Camera image path is outside the current observation")
            loaded = image_loader(str(path))
            if not isinstance(loaded, dict) or loaded.get("mime_type") != "image/jpeg":
                raise InfrastructureError("Frozen contact-sheet strategy requires a JPEG image")
            data = loaded.get("data")
            if not isinstance(data, bytes) or not data.startswith(b"\xff\xd8\xff"):
                raise InfrastructureError("Image loader returned invalid JPEG data")
            digest = hashlib.sha256(data).hexdigest()
            if loaded.get("sha256", digest) != digest:
                raise InfrastructureError("Image loader digest mismatch")
            image_path = image_dir / (digest + ".jpg")
            if not image_path.exists():
                image_path.write_bytes(data)
            data_url = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
            image_refs[data_url] = {"externalized_data_url": True,
                                    "path": image_path.relative_to(output_dir).as_posix(),
                                    "mime_type": "image/jpeg", "sha256": digest,
                                    "source_observation": path.parent.name,
                                    "source_archive_path": loaded.get("archive_path")}
            last_image_path = str(path)
            return {"role": "user", "content": [
                {"type": "input_text", "text": json.dumps({"observation": public,
                    "native_step_limit": step_limit, "remaining_steps": max(0, step_limit - obs["step_count"])},
                    ensure_ascii=False, allow_nan=False)},
                {"type": "input_image", "image_url": data_url, "detail": "high"},
            ]}

        def finish(status: str, reason: str, **extra: Any) -> dict:
            result = {"status": status, "reason": reason, "last_observation": observation,
                      "model_calls": model_calls, "accepted_action_calls": accepted_action_calls,
                      "validation_errors": validation_errors, **extra}
            record("episode_result", result=result)
            return result

        def fresh_observe() -> dict:
            record("tool_dispatch", automatic=True, request={"op": "observe"})
            result = execute_tool({"op": "observe"})
            record("tool_result", automatic=True, response=result)
            return _unwrap(result)

        history: list[dict] = [{"role": "developer", "content": [
            {"type": "input_text", "text": POLICY + "\n\nFixed guide:\n" + self._guide},
        ]}, {"role": "user", "content": [{"type": "input_text", "text": INITIAL_PROMPT}]},
        {"role": "user", "content": [{"type": "input_text", "text": json.dumps({
            "context": {k: context[k] for k in ("task", "seed") if k in context},
            "contract": filtered_contract, "native_step_limit": step_limit,
        }, ensure_ascii=False, allow_nan=False)}]}]
        record("episode_policy", context=context, policy=self.policy_manifest(),
               guide_sha256=hashlib.sha256(self._guide.encode()).hexdigest(),
               endpoint=self._endpoint, model_backend_version_verified=False)
        try:
            reason = terminal_reason(observation, step_limit)
            if reason:
                return finish("completed", reason)
            history.append(image_message(observation))
            while True:
                request = {"model": MODEL, "reasoning": {"effort": REASONING_EFFORT},
                           "input": history, "tools": copy.deepcopy(TOOLS),
                           "tool_choice": "auto", "parallel_tool_calls": False,
                           "max_output_tokens": MAX_OUTPUT_TOKENS, "store": False, "stream": True}
                response = None
                for attempt in range(len(RETRY_DELAYS) + 1):
                    model_calls += 1
                    record("model_request", model_call=model_calls, retry_index=attempt, request=request)
                    started = time.monotonic()
                    try:
                        self._stream_event_sink = lambda event: record(
                            "model_stream_event", model_call=model_calls, stream_event=event)
                        response = self._transport(copy.deepcopy(request))
                        record("model_response", model_call=model_calls,
                               latency_seconds=time.monotonic() - started, response=response)
                        break
                    except APIError as error:
                        record("model_error", model_call=model_calls, error=str(error),
                               status=error.status, retryable=error.retryable,
                               latency_seconds=time.monotonic() - started)
                        if not error.retryable or attempt == len(RETRY_DELAYS):
                            return finish("interrupted", "api_error", exception_class=type(error).__name__)
                        self._sleep(RETRY_DELAYS[attempt])
                    finally:
                        self._stream_event_sink = None
                if not isinstance(response, dict) or not isinstance(response.get("output"), list):
                    return finish("interrupted", "api_error", exception_class="MalformedAPIResponse")
                if response.get("model") != MODEL:
                    return finish("interrupted", "api_error", exception_class="UnexpectedReportedModel")
                if response.get("status") not in (None, "completed"):
                    return finish("interrupted", "api_error", exception_class="IncompleteAPIResponse")
                output = response["output"]
                history.extend(copy.deepcopy(output))
                calls = [item for item in output if item.get("type") == "function_call"]
                if not calls:
                    final_text = "\n".join(part.get("text", "") for item in output
                        if item.get("type") == "message" for part in item.get("content", [])
                        if part.get("type") == "output_text")
                    return finish("completed", "agent_gave_up", model_final_text=final_text)
                errors = []
                pending: list[tuple[dict, dict]] = []
                for call in calls:
                    call_id = call.get("call_id")
                    if not isinstance(call_id, str) or not call_id:
                        return finish("interrupted", "api_error", exception_class="MissingToolCallId")
                    try:
                        if len(calls) != 1:
                            raise ValueError("Exactly one tool call is allowed per response; none were executed")
                        if call_id in seen_call_ids:
                            raise ValueError("This tool call ID was already processed; it will not be executed twice")
                        name = call.get("name")
                        if name not in {"observe", "move", "hold", "act"}:
                            raise ValueError("Unknown tool; only observe, move, hold and act are allowed")
                        args = json.loads(call.get("arguments", ""))
                        if not isinstance(args, dict) or "op" in args:
                            raise ValueError("Tool arguments must be an object without an op override")
                        action = validate_tool({"op": name, **args}, filtered_contract,
                                               step_limit - observation["step_count"])
                        pending.append((call, action))
                    except (ValueError, TypeError) as error:
                        errors.append((call, str(error)))
                    seen_call_ids.add(call_id)
                if errors:
                    validation_errors += len(errors)
                    for call, error in errors:
                        result = {"ok": False, "error": error, "executed": False}
                        history.append({"type": "function_call_output", "call_id": call["call_id"],
                                        "output": json.dumps(result)})
                        record("tool_rejected", call_id=call["call_id"], response=result)
                    observation = fresh_observe()
                    history.append(image_message(observation))
                    continue
                call, action = pending[0]
                record("tool_dispatch", call_id=call["call_id"], automatic=False, request=action)
                # Intentionally outside all retry loops: an ambiguous tool error
                # must never cause a second execution of a robot action.
                result = execute_tool(copy.deepcopy(action))
                record("tool_result", call_id=call["call_id"], automatic=False, response=result)
                if not isinstance(result, dict) or "ok" not in result:
                    raise InfrastructureError("Tool returned an invalid bridge response")
                if result.get("infrastructure_error"):
                    raise InfrastructureError("Bridge reported an infrastructure failure")
                if result["ok"] is True:
                    observation = _unwrap(result)
                    if action["op"] != "observe":
                        accepted_action_calls += 1
                    tool_result = {"ok": True, "observation": public_observation(observation)}
                else:
                    validation_errors += 1
                    tool_result = {"ok": False, "error": self._scrub(str(result.get("error", "Tool rejected")))}
                history.append({"type": "function_call_output", "call_id": call["call_id"],
                                "output": json.dumps(tool_result, ensure_ascii=False, allow_nan=False)})
                reason = terminal_reason(observation, step_limit)
                if reason:
                    return finish("completed", reason)
                if result["ok"] is not True or not observation.get("contact_sheet"):
                    observation = fresh_observe()
                history.append(image_message(observation))
        except Exception as error:
            record("infrastructure_error", exception_class=type(error).__name__,
                   error=self._scrub(str(error)))
            return finish("interrupted", "infrastructure_error", exception_class=type(error).__name__)
