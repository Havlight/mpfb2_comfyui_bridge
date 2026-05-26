import json

from aiohttp import web
from server import PromptServer

from .bridge_state import (
    bridge_root,
    decode_base64_image,
    get_latest,
    image_abs_path,
    load_config,
    require_allowed_request,
    save_image,
    save_pose,
)
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS


WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


def _public_metadata(metadata):
    if not isinstance(metadata, dict):
        return metadata
    clean = dict(metadata)
    # The pose payload can be large. Keep it on pose websocket events, omit it
    # from generic latest/status-style responses unless explicitly needed.
    return clean


def _send_bridge_event(event_name, payload):
    try:
        PromptServer.instance.send_sync(event_name, payload)
    except Exception as exc:
        print(f"[ComfyUI-Blender-Bridge] Failed to send websocket event {event_name}: {exc}")


def _json_error(message, status=400):
    return web.json_response({"ok": False, "error": str(message)}, status=status)


def _metadata_from_payload(payload):
    metadata = payload.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    for key in (
        "target_bridge_id",
        "batch_id",
        "batch_expected_channels",
        "projection_mode",
        "pose_resolution",
        "render_resolution",
        "auto_sync",
        "pause_if_editor_open",
        "projection_warning",
        "camera_name",
        "frame",
        "render_border_disabled",
        "depth_normalization",
    ):
        if key in payload and key not in metadata:
            metadata[key] = payload[key]
    return metadata


@PromptServer.instance.routes.get("/blender_bridge/status")
async def blender_bridge_status(request):
    try:
        config = require_allowed_request(request)
    except PermissionError as exc:
        return _json_error(exc, status=403)
    return web.json_response({
        "ok": True,
        "name": "ComfyUI-Blender-Bridge",
        "bridge_root": bridge_root(),
        "allow_remote": bool(config.get("allow_remote")),
        "token_required_for_remote": bool(config.get("token")),
        "max_image_bytes": config.get("max_image_bytes"),
    })


@PromptServer.instance.routes.post("/blender_bridge/pose")
async def blender_bridge_pose(request):
    try:
        config = require_allowed_request(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            return _json_error("JSON body must be an object")
        metadata = _metadata_from_payload(payload)
        source_id = payload.get("source_id", "blender")
        pose_json = payload.get("pose_json")
        if pose_json is None:
            return _json_error("Missing pose_json")
        saved = save_pose(source_id, pose_json, metadata)
        event_payload = _public_metadata(saved)
        _send_bridge_event("blender_bridge.pose", event_payload)
        return web.json_response({"ok": True, "metadata": saved, "allow_remote": bool(config.get("allow_remote"))})
    except PermissionError as exc:
        return _json_error(exc, status=403)
    except ValueError as exc:
        return _json_error(exc, status=400)
    except Exception as exc:
        return _json_error(exc, status=500)


async def _read_multipart_image(request):
    reader = await request.multipart()
    fields = {}
    image_bytes = None
    async for part in reader:
        if part.name == "image":
            image_bytes = await part.read(decode=False)
        else:
            value = await part.text()
            fields[part.name] = value
    if "metadata" in fields:
        try:
            fields["metadata"] = json.loads(fields["metadata"])
        except Exception:
            fields["metadata"] = {}
    if "batch_expected_channels" in fields:
        try:
            fields["batch_expected_channels"] = json.loads(fields["batch_expected_channels"])
        except Exception:
            pass
    return fields, image_bytes


@PromptServer.instance.routes.post("/blender_bridge/image")
async def blender_bridge_image(request):
    try:
        config = require_allowed_request(request)
        if request.content_type and request.content_type.startswith("multipart/"):
            payload, image_bytes = await _read_multipart_image(request)
        else:
            payload = await request.json()
            if not isinstance(payload, dict):
                return _json_error("JSON body must be an object")
            image_bytes = decode_base64_image(payload.get("image_base64"))

        if not image_bytes:
            return _json_error("Missing image")
        source_id = payload.get("source_id", "blender")
        channel = payload.get("channel", "")
        metadata = _metadata_from_payload(payload)
        saved = save_image(
            source_id,
            channel,
            image_bytes,
            metadata,
            max_bytes=config.get("max_image_bytes"),
        )
        _send_bridge_event("blender_bridge.image", _public_metadata(saved))
        return web.json_response({"ok": True, "metadata": saved})
    except PermissionError as exc:
        return _json_error(exc, status=403)
    except ValueError as exc:
        return _json_error(exc, status=400)
    except Exception as exc:
        return _json_error(exc, status=500)


@PromptServer.instance.routes.get("/blender_bridge/latest")
async def blender_bridge_latest(request):
    try:
        require_allowed_request(request)
        source_id = request.query.get("source_id", "blender")
        channel = request.query.get("channel", "pose")
        metadata = get_latest(source_id, channel)
        if metadata is None:
            return web.json_response({"ok": False, "status": "missing"}, status=404)
        return web.json_response({"ok": True, "metadata": metadata})
    except PermissionError as exc:
        return _json_error(exc, status=403)
    except ValueError as exc:
        return _json_error(exc, status=400)
    except Exception as exc:
        return _json_error(exc, status=500)


@PromptServer.instance.routes.get("/blender_bridge/preview/{source_id}/{channel}.png")
async def blender_bridge_preview(request):
    try:
        require_allowed_request(request)
        source_id = request.match_info.get("source_id", "blender")
        channel = request.match_info.get("channel", "")
        metadata = get_latest(source_id, channel)
        if metadata is None:
            return web.json_response({"ok": False, "status": "missing"}, status=404)
        path = image_abs_path(metadata)
        if not path:
            return _json_error("Preview is only available for image channels", status=400)
        return web.FileResponse(path, headers={"Content-Type": "image/png"})
    except PermissionError as exc:
        return _json_error(exc, status=403)
    except ValueError as exc:
        return _json_error(exc, status=400)
    except FileNotFoundError:
        return web.json_response({"ok": False, "status": "missing"}, status=404)
    except Exception as exc:
        return _json_error(exc, status=500)


_config = load_config()
print(
    "[ComfyUI-Blender-Bridge] Loaded. "
    f"Remote access={'enabled' if _config.get('allow_remote') else 'loopback-only'}."
)
