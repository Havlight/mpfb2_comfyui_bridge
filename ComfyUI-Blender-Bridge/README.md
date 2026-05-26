# ComfyUI Blender Bridge

Independent ComfyUI custom node pack for receiving MPFB2/Blender OpenPose, beauty, and depth data.

## Install

Place this folder in `ComfyUI/custom_nodes/ComfyUI-Blender-Bridge` and restart ComfyUI.

The package exposes:

- `Blender Bridge Pose Receiver`
- `Blender Bridge Image Receiver`
- `POST /blender_bridge/pose`
- `POST /blender_bridge/image`
- `GET /blender_bridge/status`
- `GET /blender_bridge/latest`
- `GET /blender_bridge/preview/<source_id>/<channel>.png`

It also loads `js/bridge.js` through `WEB_DIRECTORY` and listens for realtime bridge websocket events. Pose events update matching OpenPose Studio nodes immediately, and image events update matching image receiver node thumbnails without requiring a workflow execution. Beauty previews also draw the latest pose keypoints as an overlay when available.

## Security

By default, routes accept loopback clients only (`127.0.0.1`, `::1`, `localhost`).

For remote access, create `config.json` next to this README:

```json
{
  "allow_remote": true,
  "token": "change-me",
  "max_image_bytes": 52428800
}
```

Remote Blender clients must send the same token in `X-Blender-Bridge-Token`.

## State

Images and sidecars are written under ComfyUI's input directory:

```text
input/blender_bridge/<source_id>/beauty.png
input/blender_bridge/<source_id>/beauty.json
input/blender_bridge/<source_id>/depth.png
input/blender_bridge/<source_id>/depth.json
input/blender_bridge/<source_id>/pose.json
input/blender_bridge/<source_id>/batches/<batch_id>.json
```

Receiver nodes use `update_id` and content hashes in `IS_CHANGED`, so ComfyUI will re-run when Blender sends new data.
