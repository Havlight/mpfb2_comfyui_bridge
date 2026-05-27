import datetime
import json
import os
import tempfile

import bpy


DEFAULT_SAVE_DIR = "//mpfb_comfy_bridge_exports"
IMAGE_CHANNELS = {"beauty", "depth"}


def make_timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def resolve_save_dir(directory):
    value = str(directory or "").strip() or DEFAULT_SAVE_DIR
    if value.startswith("//") and not bpy.data.filepath:
        suffix = value[2:].lstrip("/\\") or "mpfb_comfy_bridge_exports"
        folder = os.path.join(bpy.app.tempdir or tempfile.gettempdir(), suffix)
    else:
        folder = bpy.path.abspath(value)
    folder = os.path.abspath(os.path.expanduser(folder))
    os.makedirs(folder, exist_ok=True)
    return folder


def write_pose_json(directory, timestamp, pose):
    folder = resolve_save_dir(directory)
    path = _unique_path(folder, timestamp, "pose", "json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(pose, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def write_png(directory, timestamp, channel, image_bytes):
    channel = str(channel or "").strip().lower()
    if channel not in IMAGE_CHANNELS:
        raise ValueError("Local save only supports beauty and depth images")
    folder = resolve_save_dir(directory)
    path = _unique_path(folder, timestamp, channel, "png")
    with open(path, "wb") as handle:
        handle.write(image_bytes)
    return path


def _unique_path(folder, timestamp, label, extension):
    stem = f"{timestamp}_{label}"
    path = os.path.join(folder, f"{stem}.{extension}")
    if not os.path.exists(path):
        return path
    for index in range(1, 1000):
        path = os.path.join(folder, f"{stem}_{index:03d}.{extension}")
        if not os.path.exists(path):
            return path
    raise FileExistsError(f"Could not create a unique local save filename for {stem}")
