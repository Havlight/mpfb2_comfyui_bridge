import base64
import hashlib
import json
import os
import re
import tempfile
import time
from threading import Lock

try:
    import folder_paths
except Exception:
    folder_paths = None


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
IMAGE_CHANNELS = {"beauty", "depth"}
POSE_CHANNEL = "pose"
ALL_CHANNELS = IMAGE_CHANNELS | {POSE_CHANNEL}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_MAX_IMAGE_BYTES = 50 * 1024 * 1024
PUBLIC_METADATA_KEYS = (
    "auto_sync",
    "pause_if_editor_open",
    "projection_warning",
    "camera_name",
    "frame",
    "render_border_disabled",
    "depth_normalization",
)

_LOCK = Lock()


def _package_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _input_dir():
    if folder_paths is not None:
        try:
            return folder_paths.get_input_directory()
        except Exception:
            pass
    return os.path.join(_package_dir(), "input")


def bridge_root():
    return os.path.join(_input_dir(), "blender_bridge")


def load_config():
    config = {
        "allow_remote": False,
        "token": "",
        "max_image_bytes": DEFAULT_MAX_IMAGE_BYTES,
    }
    config_path = os.path.join(_package_dir(), "config.json")
    try:
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                config.update(loaded)
    except Exception:
        pass

    allow_remote = os.environ.get("BLENDER_BRIDGE_ALLOW_REMOTE")
    if allow_remote is not None:
        config["allow_remote"] = allow_remote.strip().lower() in ("1", "true", "yes", "on")

    token = os.environ.get("BLENDER_BRIDGE_TOKEN")
    if token is not None:
        config["token"] = token

    max_bytes = os.environ.get("BLENDER_BRIDGE_MAX_IMAGE_BYTES")
    if max_bytes:
        try:
            config["max_image_bytes"] = int(max_bytes)
        except Exception:
            pass

    if not isinstance(config.get("token"), str):
        config["token"] = ""
    try:
        config["max_image_bytes"] = int(config.get("max_image_bytes", DEFAULT_MAX_IMAGE_BYTES))
    except Exception:
        config["max_image_bytes"] = DEFAULT_MAX_IMAGE_BYTES
    config["max_image_bytes"] = max(1024, config["max_image_bytes"])
    config["allow_remote"] = bool(config.get("allow_remote"))
    return config


def is_loopback(remote):
    if not remote:
        return False
    host = str(remote).split("%", 1)[0]
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    if host.startswith("127."):
        return True
    return False


def require_allowed_request(request, token=None):
    config = load_config()
    remote = getattr(request, "remote", None)
    if is_loopback(remote):
        return config

    expected = config.get("token") or ""
    supplied = token or request.headers.get("X-Blender-Bridge-Token") or request.query.get("token") or ""
    if config.get("allow_remote") and expected and supplied == expected:
        return config
    raise PermissionError("Remote bridge access is disabled or token is invalid")


def validate_source_id(source_id):
    source = str(source_id or "").strip()
    if not SAFE_ID_RE.match(source):
        raise ValueError("Invalid source_id")
    return source


def validate_channel(channel, allow_pose=False):
    value = str(channel or "").strip().lower()
    allowed = ALL_CHANNELS if allow_pose else IMAGE_CHANNELS
    if value not in allowed:
        raise ValueError("Invalid channel")
    return value


def safe_batch_id(batch_id):
    if batch_id is None or str(batch_id).strip() == "":
        return None
    value = str(batch_id).strip()
    if not SAFE_ID_RE.match(value):
        raise ValueError("Invalid batch_id")
    return value


def normalize_expected_channels(value):
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        return []
    output = []
    for item in value:
        channel = str(item or "").strip().lower()
        if channel in ALL_CHANNELS and channel not in output:
            output.append(channel)
    return output


def public_extra_metadata(metadata):
    if not isinstance(metadata, dict):
        return {}
    return {key: metadata[key] for key in PUBLIC_METADATA_KEYS if key in metadata}


def source_dir(source_id):
    source = validate_source_id(source_id)
    return os.path.join(bridge_root(), source)


def batches_dir(source_id):
    return os.path.join(source_dir(source_id), "batches")


def _atomic_write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _atomic_write_json(path, payload):
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(path, data)


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _metadata_path(source_id, channel):
    source = validate_source_id(source_id)
    channel = validate_channel(channel, allow_pose=True)
    return os.path.join(source_dir(source), f"{channel}.json")


def _image_path(source_id, channel):
    source = validate_source_id(source_id)
    channel = validate_channel(channel)
    return os.path.join(source_dir(source), f"{channel}.png")


def _batch_path(source_id, batch_id):
    source = validate_source_id(source_id)
    batch = safe_batch_id(batch_id)
    if not batch:
        raise ValueError("Missing batch_id")
    return os.path.join(batches_dir(source), f"{batch}.json")


def _next_update_id(source_id, channel):
    existing = _read_json(_metadata_path(source_id, channel), {})
    try:
        return int(existing.get("update_id", 0)) + 1
    except Exception:
        return 1


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def hash_json(payload):
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hash_bytes(data)


def validate_pose_payload(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception as exc:
            raise ValueError("pose_json must be valid JSON") from exc
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        value = value[0]
    if not isinstance(value, dict):
        raise ValueError("pose_json must be an object")

    people = value.get("people")
    if people is None and isinstance(value.get("pose_keypoints_2d"), list):
        people = [value]
    if not isinstance(people, list):
        raise ValueError("pose_json must contain people")

    width = int(float(value.get("canvas_width", value.get("width", 512))))
    height = int(float(value.get("canvas_height", value.get("height", 512))))
    if width <= 0 or height <= 0:
        raise ValueError("Invalid canvas size")

    normalized_people = []
    for person in people:
        if not isinstance(person, dict):
            continue
        pose = person.get("pose_keypoints_2d")
        if not isinstance(pose, list) or len(pose) < 34:
            continue
        clean = dict(person)
        clean["pose_keypoints_2d"] = [float(x) if isinstance(x, (int, float)) else 0.0 for x in pose]
        for key in ("face_keypoints_2d", "hand_left_keypoints_2d", "hand_right_keypoints_2d"):
            if key in clean and isinstance(clean[key], list):
                clean[key] = [float(x) if isinstance(x, (int, float)) else 0.0 for x in clean[key]]
        normalized_people.append(clean)

    if not normalized_people:
        raise ValueError("pose_json contains no valid people")

    return {
        **{k: v for k, v in value.items() if k not in ("width", "height", "keypoints", "people")},
        "canvas_width": width,
        "canvas_height": height,
        "people": normalized_people,
    }


def png_dimensions(data):
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Image must be PNG")
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ValueError("Invalid PNG header")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError("Invalid PNG dimensions")
    return width, height


def update_batch(source_id, batch_id, expected_channels, received_channel, metadata):
    if not batch_id:
        return None
    expected = normalize_expected_channels(expected_channels)
    if not expected:
        expected = [received_channel]
    batch_path = _batch_path(source_id, batch_id)
    existing = _read_json(batch_path, {})
    received = existing.get("received", {}) if isinstance(existing.get("received"), dict) else {}
    received[received_channel] = {
        "update_id": metadata.get("update_id"),
        "hash": metadata.get("hash"),
        "mtime": metadata.get("mtime"),
    }
    complete = all(channel in received for channel in expected)
    batch_meta = {
        "source_id": source_id,
        "batch_id": batch_id,
        "expected_channels": expected,
        "received": received,
        "complete": complete,
        "updated_at": time.time(),
    }
    _atomic_write_json(batch_path, batch_meta)
    return batch_meta


def save_pose(source_id, pose_json, metadata=None):
    source = validate_source_id(source_id)
    pose = validate_pose_payload(pose_json)
    metadata = metadata if isinstance(metadata, dict) else {}
    batch_id = safe_batch_id(metadata.get("batch_id"))
    expected = normalize_expected_channels(metadata.get("batch_expected_channels"))
    now = time.time()
    digest = hash_json(pose)

    with _LOCK:
        update_id = _next_update_id(source, POSE_CHANNEL)
        output = {
            "source_id": source,
            "channel": POSE_CHANNEL,
            "update_id": update_id,
            "hash": digest,
            "mtime": now,
            "batch_id": batch_id,
            "target_bridge_id": metadata.get("target_bridge_id") or "",
            "projection_mode": metadata.get("projection_mode") or "",
            "pose_resolution": metadata.get("pose_resolution") or [pose["canvas_width"], pose["canvas_height"]],
            "render_resolution": metadata.get("render_resolution") or None,
            "pose_json": pose,
        }
        output.update(public_extra_metadata(metadata))
        batch = update_batch(source, batch_id, expected, POSE_CHANNEL, output)
        if batch:
            output["batch_complete"] = batch.get("complete", False)
        _atomic_write_json(_metadata_path(source, POSE_CHANNEL), output)
        return output


def save_image(source_id, channel, image_bytes, metadata=None, max_bytes=None):
    source = validate_source_id(source_id)
    channel = validate_channel(channel)
    metadata = metadata if isinstance(metadata, dict) else {}
    max_bytes = int(max_bytes or load_config().get("max_image_bytes", DEFAULT_MAX_IMAGE_BYTES))
    if len(image_bytes) > max_bytes:
        raise ValueError("Image is larger than the configured maximum")
    width, height = png_dimensions(image_bytes)
    batch_id = safe_batch_id(metadata.get("batch_id"))
    expected = normalize_expected_channels(metadata.get("batch_expected_channels"))
    now = time.time()
    digest = hash_bytes(image_bytes)
    rel_path = os.path.join("blender_bridge", source, f"{channel}.png").replace("\\", "/")

    with _LOCK:
        update_id = _next_update_id(source, channel)
        path = _image_path(source, channel)
        _atomic_write_bytes(path, image_bytes)
        output = {
            "source_id": source,
            "channel": channel,
            "update_id": update_id,
            "hash": digest,
            "mtime": now,
            "batch_id": batch_id,
            "target_bridge_id": metadata.get("target_bridge_id") or "",
            "projection_mode": metadata.get("projection_mode") or "",
            "pose_resolution": metadata.get("pose_resolution") or None,
            "render_resolution": metadata.get("render_resolution") or [width, height],
            "width": width,
            "height": height,
            "relative_path": rel_path,
        }
        output.update(public_extra_metadata(metadata))
        batch = update_batch(source, batch_id, expected, channel, output)
        if batch:
            output["batch_complete"] = batch.get("complete", False)
        _atomic_write_json(_metadata_path(source, channel), output)
        return output


def get_latest(source_id, channel):
    source = validate_source_id(source_id)
    channel = validate_channel(channel, allow_pose=True)
    return _read_json(_metadata_path(source, channel), None)


def get_batch(source_id, batch_id):
    return _read_json(_batch_path(source_id, batch_id), None)


def is_complete_batch(metadata):
    if not isinstance(metadata, dict):
        return False
    batch_id = metadata.get("batch_id")
    if not batch_id:
        return True
    batch = get_batch(metadata.get("source_id"), batch_id)
    return bool(batch and batch.get("complete"))


def image_abs_path(metadata):
    if not isinstance(metadata, dict):
        return None
    channel = metadata.get("channel")
    if channel not in IMAGE_CHANNELS:
        return None
    return _image_path(metadata.get("source_id"), channel)


def fingerprint(source_id, channel, require_complete_batch=False, target_bridge_id=""):
    try:
        meta = get_latest(source_id, channel)
    except Exception as exc:
        return f"missing:{source_id}:{channel}:{target_bridge_id}:{exc}"
    if not meta:
        return f"missing:{source_id}:{channel}:{target_bridge_id}"
    if target_bridge_id and meta.get("target_bridge_id") not in ("", target_bridge_id):
        return f"target-mismatch:{source_id}:{channel}:{target_bridge_id}:{meta.get('update_id', 0)}"
    if require_complete_batch and not is_complete_batch(meta):
        return f"incomplete:{source_id}:{channel}:{meta.get('batch_id')}:{meta.get('update_id')}"
    return json.dumps({
        "source_id": source_id,
        "channel": channel,
        "target_bridge_id": target_bridge_id or "",
        "update_id": meta.get("update_id"),
        "hash": meta.get("hash"),
        "batch_id": meta.get("batch_id"),
        "batch_complete": is_complete_batch(meta),
    }, sort_keys=True)


def decode_base64_image(data):
    if not isinstance(data, str):
        raise ValueError("image_base64 must be a string")
    if "," in data and data.split(",", 1)[0].startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)
