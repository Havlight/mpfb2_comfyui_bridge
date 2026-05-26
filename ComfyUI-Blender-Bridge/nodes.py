import json
import os

import numpy as np
import torch
from PIL import Image, ImageOps

from .bridge_state import (
    IMAGE_CHANNELS,
    fingerprint,
    get_latest,
    image_abs_path,
    is_complete_batch,
    validate_pose_payload,
)


EMPTY_POSE = {
    "canvas_width": 512,
    "canvas_height": 512,
    "people": [],
}


def _metadata_json(metadata):
    try:
        return json.dumps(metadata or {}, sort_keys=True)
    except Exception:
        return "{}"


def _empty_image(width=64, height=64):
    image = torch.zeros((1, height, width, 3), dtype=torch.float32)
    mask = torch.zeros((1, height, width), dtype=torch.float32)
    return image, mask


def _pose_to_pose_keypoint(pose_json):
    try:
        return validate_pose_payload(pose_json)
    except Exception:
        return dict(EMPTY_POSE)


def _load_png_tensor(path, channel):
    if not path or not os.path.isfile(path):
        return _empty_image()

    img = Image.open(path)
    img = ImageOps.exif_transpose(img)

    alpha = None
    if img.mode == "RGBA":
        alpha = np.asarray(img.getchannel("A")).astype(np.float32) / 255.0

    if channel == "depth":
        gray = ImageOps.grayscale(img)
        gray_np = np.asarray(gray).astype(np.float32) / 255.0
        rgb_np = np.stack([gray_np, gray_np, gray_np], axis=-1)
        mask_np = gray_np
    else:
        rgb = img.convert("RGB")
        rgb_np = np.asarray(rgb).astype(np.float32) / 255.0
        mask_np = 1.0 - alpha if alpha is not None else np.zeros(rgb_np.shape[:2], dtype=np.float32)

    image = torch.from_numpy(rgb_np).unsqueeze(0)
    mask = torch.from_numpy(mask_np.astype(np.float32)).unsqueeze(0)
    return image, mask


class BlenderBridgePoseReceiver:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_id": ("STRING", {"default": "blender", "multiline": False}),
                "target_bridge_id": ("STRING", {"default": "", "multiline": False}),
                "require_complete_batch": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING", "POSE_KEYPOINT", "STRING")
    RETURN_NAMES = ("pose_json", "kps", "metadata")
    FUNCTION = "receive"
    CATEGORY = "Blender Bridge"

    @classmethod
    def IS_CHANGED(cls, source_id, target_bridge_id, require_complete_batch):
        return fingerprint(source_id, "pose", require_complete_batch, target_bridge_id)

    def receive(self, source_id, target_bridge_id, require_complete_batch):
        try:
            metadata = get_latest(source_id, "pose")
        except Exception as exc:
            return "", dict(EMPTY_POSE), _metadata_json({"error": str(exc)})

        if not metadata:
            return "", dict(EMPTY_POSE), _metadata_json({"status": "missing", "source_id": source_id, "channel": "pose"})

        if target_bridge_id and metadata.get("target_bridge_id") not in ("", target_bridge_id):
            return "", dict(EMPTY_POSE), _metadata_json({
                **metadata,
                "status": "target_mismatch",
                "requested_target_bridge_id": target_bridge_id,
            })

        if require_complete_batch and not is_complete_batch(metadata):
            return "", dict(EMPTY_POSE), _metadata_json({**metadata, "status": "incomplete_batch"})

        pose = metadata.get("pose_json") or EMPTY_POSE
        kps = _pose_to_pose_keypoint(pose)
        return json.dumps(pose), kps, _metadata_json(metadata)


class BlenderBridgeImageReceiver:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_id": ("STRING", {"default": "blender", "multiline": False}),
                "channel": (sorted(IMAGE_CHANNELS), {"default": "beauty"}),
                "require_complete_batch": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "mask", "metadata")
    FUNCTION = "receive"
    CATEGORY = "Blender Bridge"

    @classmethod
    def IS_CHANGED(cls, source_id, channel, require_complete_batch):
        return fingerprint(source_id, channel, require_complete_batch)

    def receive(self, source_id, channel, require_complete_batch):
        try:
            metadata = get_latest(source_id, channel)
        except Exception as exc:
            image, mask = _empty_image()
            return image, mask, _metadata_json({"error": str(exc)})

        if not metadata:
            image, mask = _empty_image()
            return image, mask, _metadata_json({"status": "missing", "source_id": source_id, "channel": channel})

        if require_complete_batch and not is_complete_batch(metadata):
            image, mask = _empty_image()
            return image, mask, _metadata_json({**metadata, "status": "incomplete_batch"})

        try:
            image, mask = _load_png_tensor(image_abs_path(metadata), channel)
            return image, mask, _metadata_json(metadata)
        except Exception as exc:
            image, mask = _empty_image()
            return image, mask, _metadata_json({**metadata, "error": str(exc)})


NODE_CLASS_MAPPINGS = {
    "BlenderBridgePoseReceiver": BlenderBridgePoseReceiver,
    "BlenderBridgeImageReceiver": BlenderBridgeImageReceiver,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BlenderBridgePoseReceiver": "Blender Bridge Pose Receiver",
    "BlenderBridgeImageReceiver": "Blender Bridge Image Receiver",
}
