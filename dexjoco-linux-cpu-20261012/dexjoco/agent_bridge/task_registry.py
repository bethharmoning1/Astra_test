"""Allowlisted task metadata from the pinned upstream configs/rand_obj/*.yaml.
This is interface metadata only, not a manipulation strategy or task solution.
"""
from pathlib import Path
import hashlib

DEFAULT_TASK = "click_mouse"
CAMERA_PROFILE = "rand_obj"
# The deployment has configs/ at its root; the local pinned copy lives in upstream/.
_CONFIG_ROOT = Path(__file__).resolve().parent.parent / "configs"
if not _CONFIG_ROOT.is_dir():
    _CONFIG_ROOT = Path(__file__).resolve().parent / "upstream" / "configs"
CAMERA_METADATA_FIELDS = ("camera_profile", "camera_mapping_source", "camera_mapping", "camera_config_sha256")

TASKS = {
    "click_mouse": {
        "prompt": "Move the mouse to the purple mouse pad and click the left mouse button.",
        "description_zh": "将鼠标移到紫色鼠标垫上，并点击鼠标左键。",
    },
    "fold_glasses": {
        "prompt": "Fold the glasses and place them into the case.",
        "description_zh": "折叠眼镜并放入眼镜盒。",
    },
    "hammer_nail": {
        "prompt": "Use the hammer to drive the nail into the wooden board.",
        "description_zh": "使用锤子将钉子钉入木板。",
    },
    "pick_bucket": {
        "prompt": "Place the boxed food into the bucket and then lift the bucket.",
        "description_zh": "将盒装食物放入桶中，然后提起桶。",
    },
    "pinch_tongs": {
        "prompt": "Grasp the tongs and perform three consecutive open-close motions.",
        "description_zh": "抓住夹钳并连续完成三次张开—闭合动作。",
    },
    "water_plant": {
        "prompt": "Grasp the watering can and apply water to the plant.",
        "description_zh": "抓住浇水器并给植物浇水。",
    },
    "bimanual_assembly": {
        "prompt": "Grasp the tray with the left hand and the peg with the right hand, then insert the peg into the hole.",
        "description_zh": "左手抓托盘、右手抓插销，将插销插入孔中。",
    },
    "bimanual_hanoi": {
        "prompt": "Execute the final two moves of the three-level Tower of Hanoi: move the medium disk from the middle peg to the right peg with the right hand, then move the small disk from the left peg to the right peg with the left hand.",
        "description_zh": "完成三层汉诺塔最后两步：右手移动中盘，左手移动小盘。",
    },
    "bimanual_microwave_cook": {
        "prompt": "Open the microwave door, place the food inside the microwave, close the door, and press the start button.",
        "description_zh": "打开微波炉、放入食物、关门并按启动键。",
    },
    "bimanual_photograph": {
        "prompt": "Grasp the camera with the left hand, align it with the logo, and press the shutter button with the right hand.",
        "description_zh": "左手持相机对准标志，右手按快门。",
    },
    "bimanual_unlock_ipad": {
        "prompt": "Grasp the iPad and enter the password 123 to unlock the device.",
        "description_zh": "拿起 iPad 并输入密码 123 解锁。",
    },
}


def camera_configuration(name, config_root=None):
    """Select only the official per-task policy views, never all native images.

    This bridge deliberately uses rand_obj, not rand_full or multi_task. In the
    latter, single-arm wrist_left/right are duplicate inputs, not distinct views.
    Missing or invalid mappings fail closed instead of exposing extra cameras.
    """
    if name not in TASKS:
        raise ValueError("Unknown task; run 'tasks' to see the allowlist")
    import yaml
    config_root = Path(config_root) if config_root is not None else _CONFIG_ROOT
    path = config_root / CAMERA_PROFILE / (name + ".yaml")
    if not path.is_file():
        raise ValueError("Official camera config is missing: " + str(path))
    data = path.read_bytes()
    config = yaml.safe_load(data)
    dual = name.startswith("bimanual_")
    robot_type = "dual_arm" if dual else "single_arm"
    roles = ("base", "wrist_left", "wrist_right") if dual else ("base", "wrist")
    native = {"ego", "wrist_left", "wrist_right"} if dual else (
        {"front", "wrist"} if name == "water_plant" else {"ego_right", "wrist", "front", "ego_left"})
    if not isinstance(config, dict) or config.get("env_name") != name or config.get("robot_type") != robot_type:
        raise ValueError("Camera config task/robot mismatch: " + str(path))
    mapping = config.get("camera_mapping")
    if not isinstance(mapping, dict) or set(mapping) != set(roles):
        raise ValueError("Expected official per-task camera roles: " + str(roles))
    mapping = {role: mapping[role] for role in roles}
    values = list(mapping.values())
    if any(not isinstance(value, str) or value not in native for value in values):
        raise ValueError("Camera mapping refers to unavailable task RGB views")
    if len(set(values)) != len(roles):
        raise ValueError("Per-task camera mapping must use distinct views; multi_task duplicates are not enabled")
    return {
        "camera_profile": CAMERA_PROFILE,
        "camera_mapping_source": "configs/" + CAMERA_PROFILE + "/" + name + ".yaml",
        "camera_config_sha256": hashlib.sha256(data).hexdigest(),
        "camera_mapping": mapping,
        "camera_keys": values,
    }


def metadata(name):
    if name not in TASKS:
        raise ValueError("Unknown task; run 'tasks' to see the allowlist")
    bimanual = name.startswith("bimanual_")
    return {
        "task": name, **TASKS[name],
        "robot_type": "dual_arm" if bimanual else "single_arm",
        "native_action_dim": 46 if bimanual else 23,
        **camera_configuration(name),
        "bridge_supported": True,
        "prompt_source": f"configs/rand_obj/{name}.yaml",
        "unsupported_reason": None,
    }


def require_supported(name):
    info = metadata(name)
    if not info["bridge_supported"]:
        raise ValueError(f"Task {name} is not bridge-supported: {info['unsupported_reason']}")
    return info


def catalog():
    return {"default_task": DEFAULT_TASK, "camera_profile": CAMERA_PROFILE,
            "camera_selection": "official per-task camera_mapping; agent and read-only preview share the same views",
            "tasks": [metadata(name) for name in TASKS],
            "task_switch": "Confirm ownership, stop current runner, then start --task NAME; start/reset never silently switch tasks"}
