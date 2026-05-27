# MPFB2 ComfyUI Bridge

Bridge packages for sending MPFB2/Blender camera-space pose, beauty renders, and depth maps to ComfyUI with realtime OpenPose Studio and image receiver previews.

This repository contains two separate runtime packages:

- `mpfb_comfy_bridge`: Blender 4.5 addon that extends an MPFB2 workflow.
- `ComfyUI-Blender-Bridge`: ComfyUI custom node pack, HTTP bridge routes, websocket events, and frontend preview integration.

## What It Does

- Sends MPFB2 OpenPose JSON from Blender to ComfyUI.
- Updates a target `OpenPoseStudio` node immediately through websocket events.
- Sends beauty render PNGs to ComfyUI.
- Sends camera depth PNGs to ComfyUI.
- Shows received beauty/depth images directly on `Blender Bridge Image Receiver` nodes without running the workflow.
- Draws the latest pose keypoints over beauty previews when pose data is available.
- Keeps workflow execution support through receiver nodes for normal ComfyUI graph usage.

Realtime preview is handled by websocket frontend events. Node inputs and outputs are still useful for workflow execution, but they are not the realtime update mechanism.

## Requirements

- Blender 4.5.
- MPFB2 installed and enabled in Blender.
- ComfyUI.
- ComfyUI-OpenPose-Studio installed if you want realtime OpenPose Studio updates.
- Browser hard refresh after installing or updating the ComfyUI node pack frontend JavaScript.

## Install

### 1. Install the ComfyUI Node Pack

Copy `ComfyUI-Blender-Bridge` into ComfyUI's `custom_nodes` directory:

```text
ComfyUI/custom_nodes/ComfyUI-Blender-Bridge
```

Restart ComfyUI. The startup log should include:

```text
[ComfyUI-Blender-Bridge] Loaded.
```

Then hard refresh the ComfyUI browser tab so the frontend extension is reloaded.

### 2. Install the Blender Addon

Copy `mpfb_comfy_bridge` into a Blender addon directory, for example:

```text
%APPDATA%/Blender Foundation/Blender/4.5/scripts/addons/mpfb_comfy_bridge
```

Enable it in Blender:

```text
Edit > Preferences > Add-ons > MPFB ComfyUI Bridge
```

Make sure MPFB2 is enabled before using the bridge. The bridge registers as a child panel of MPFB2's AI panel and does not add a new top-level N-panel tab, which helps avoid simple-tabs ordering conflicts.

## Quick Start

1. Start ComfyUI.
2. Create or open a workflow with an `OpenPoseStudio` node.
3. Right-click the `OpenPoseStudio` node and choose `Copy Bridge Target ID`.
4. Add a `Blender Bridge Pose Receiver` node if you want pose data available during workflow execution.
5. Add one or more `Blender Bridge Image Receiver` nodes for `beauty` and `depth`.
6. In Blender, open the MPFB sidebar area and find `ComfyUI Bridge`.
7. Set `ComfyUI URL`, usually:

```text
http://127.0.0.1:8188
```

8. Set `Source ID`, usually `blender`.
9. Paste the OpenPose Studio target id into `Target Bridge ID`.
10. Enable `Lock Target ID` when you want Blender to update only that OpenPose Studio node.
11. Select the MPFB armature you want to export.
12. Set `Camera Override` or use the scene camera.
13. Press `Send Pose`, `Send Beauty`, `Send Depth`, or `Send All`.

The OpenPose Studio node and matching image receiver previews should update immediately after ComfyUI receives the data.

## Blender Addon UI

The Blender addon provides:

- `ComfyUI URL`: target ComfyUI server.
- `Token`: optional bridge token for remote ComfyUI access.
- `Source ID`: sender id used by ComfyUI receiver nodes.
- `Target Bridge ID`: target OpenPose Studio node id.
- `Lock Target ID`: prevents accidental updates to other OpenPose Studio nodes.
- `Camera Override`: optional camera selector. If empty, `scene.camera` is used.
- `Hands`: include MPFB hand keypoints.
- `Send Pose`: send OpenPose JSON only.
- `Send Beauty`: send the normal Blender render.
- `Send Depth`: send a camera depth map.
- `Send All`: send pose, beauty, and depth as one batch.
- `Auto Pose Sync`: periodically sends pose only.
- `Pause when Studio editor is open`: avoids overwriting active OpenPose Studio editor changes during auto sync.

Auto sync can overwrite OpenPose Studio edits. Keep the pause option enabled when editing poses manually.

## ComfyUI Nodes

### Blender Bridge Pose Receiver

Outputs:

- `pose_json`: JSON string.
- `kps`: `POSE_KEYPOINT` data.
- `metadata`: JSON metadata string.

Inputs:

- `source_id`: sender id, usually `blender`.
- `target_bridge_id`: optional target id filter.
- `require_complete_batch`: wait for all expected batch channels.

### Blender Bridge Image Receiver

Outputs:

- `image`: ComfyUI `IMAGE`.
- `mask`: ComfyUI `MASK`.
- `metadata`: JSON metadata string.

Inputs:

- `source_id`: sender id, usually `blender`.
- `channel`: `beauty` or `depth`.
- `require_complete_batch`: wait for all expected batch channels.

The image receiver also creates a frontend preview area. When ComfyUI receives a matching `blender_bridge.image` websocket event, the preview updates immediately without workflow execution.

## Depth Output

Depth is generated in Blender as a camera-space z-buffer PNG:

- It is not affected by lights, materials, or scene color management.
- Near surfaces are white.
- Far surfaces are black.
- Empty background is black.
- Metadata includes `depth_normalization.mode = camera_zbuffer_cpu`, plus near/far range, sample count, and triangle count.

The depth pass is produced from renderable mesh geometry visible to the active view layer. Material alpha and transparency are not currently simulated by the CPU depth rasterizer.

## Camera and Render Policy

- The default camera is `context.scene.camera`.
- `Camera Override` can be used in the Blender UI.
- Pose, beauty, and depth use the same camera, frame, and effective render resolution.
- Render border and crop are temporarily disabled while sending data, then restored afterward.
- If pose keypoints project outside the camera canvas, metadata includes `projection_warning`.

## ComfyUI HTTP API

The node pack registers these routes:

```text
GET  /blender_bridge/status
GET  /blender_bridge/latest?source_id=blender&channel=pose
GET  /blender_bridge/preview/{source_id}/{channel}.png
POST /blender_bridge/pose
POST /blender_bridge/image
```

`POST /blender_bridge/image` accepts PNG data as multipart form upload or JSON base64 data.

Valid image channels are:

```text
beauty
depth
```

Valid latest channels are:

```text
pose
beauty
depth
```

## Stored State

Images and sidecar metadata are written under ComfyUI's input directory:

```text
input/blender_bridge/<source_id>/pose.json
input/blender_bridge/<source_id>/beauty.png
input/blender_bridge/<source_id>/beauty.json
input/blender_bridge/<source_id>/depth.png
input/blender_bridge/<source_id>/depth.json
input/blender_bridge/<source_id>/batches/<batch_id>.json
```

Receiver nodes use update ids and content hashes in `IS_CHANGED`, so workflow execution sees new data when Blender sends updates.

## Remote Access and Security

By default, the ComfyUI routes accept loopback clients only:

```text
127.0.0.1
::1
localhost
```

For remote Blender clients, create `ComfyUI-Blender-Bridge/config.json`:

```json
{
  "allow_remote": true,
  "token": "change-me",
  "max_image_bytes": 52428800
}
```

Then set the same token in the Blender addon UI. Remote requests must include `X-Blender-Bridge-Token`.

## Troubleshooting

### OpenPose Studio Does Not Update

- Restart ComfyUI after installing the node pack.
- Hard refresh the browser tab.
- Confirm ComfyUI-OpenPose-Studio is installed.
- Right-click the OpenPose Studio node and copy a fresh `Bridge Target ID`.
- Paste it into Blender and enable `Lock Target ID`.
- If `Lock Target ID` is disabled, the bridge falls back to the active, selected, open-editor, or first OpenPose Studio node.

### Image Receiver Preview Does Not Update

- Confirm the receiver `source_id` matches Blender's `Source ID`.
- Confirm the receiver `channel` is `beauty` or `depth`.
- Hard refresh the browser after updating the node pack.
- Check that the preview route works in a browser:

```text
http://127.0.0.1:8188/blender_bridge/preview/blender/beauty.png
http://127.0.0.1:8188/blender_bridge/preview/blender/depth.png
```

The preview should update from websocket events without running the workflow.

### Depth Looks Like a Beauty Render

Update to the latest version of this repository and restart Blender. Current depth output uses CPU camera z-buffer generation and should not contain lighting or material shading.

### Depth Is Empty or Mostly Black

- Make sure the mesh is visible and renderable.
- Make sure the object is in front of the active scene camera.
- Make sure the active view layer contains the mesh.
- Try `Send Beauty` to confirm camera framing, then `Send Depth`.

### Blender Says `No module named 'mpfb'`

Make sure MPFB2 is installed and enabled before enabling or using this bridge. The bridge includes runtime lookup for MPFB2 modules loaded as either `mpfb` or extension-namespaced packages, but MPFB2 still has to be active in Blender.

### Connection Check Fails

- Confirm ComfyUI is running.
- Confirm the URL is correct, usually `http://127.0.0.1:8188`.
- Open this URL in a browser:

```text
http://127.0.0.1:8188/blender_bridge/status
```

If using remote access, confirm `config.json` and the Blender token match.

## Development Checks

From the repository root:

```powershell
node --check .\ComfyUI-Blender-Bridge\js\bridge.js
py -3 -m py_compile .\ComfyUI-Blender-Bridge\bridge_state.py .\ComfyUI-Blender-Bridge\nodes.py .\ComfyUI-Blender-Bridge\__init__.py
```

With Blender 4.5 installed in the default Windows location:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.5\4.5\python\bin\python.exe' -m py_compile .\mpfb_comfy_bridge\capture.py .\mpfb_comfy_bridge\ui.py .\mpfb_comfy_bridge\http_queue.py
```

The ComfyUI node pack also includes Python tests under:

```text
ComfyUI-Blender-Bridge/tests
```

Run them in an environment with `pytest` installed.

## Current Limitations

- Render border and crop alignment are not supported in the MVP; they are temporarily disabled during sends.
- Depth rasterization does not model material transparency.
- Auto sync sends pose only. Beauty and depth are sent manually.
- The Blender addon currently targets MPFB default rigs.

## Repository Layout

```text
mpfb2_comfyui_bridge/
  ComfyUI-Blender-Bridge/
    __init__.py
    bridge_state.py
    nodes.py
    js/bridge.js
    tests/
  mpfb_comfy_bridge/
    __init__.py
    capture.py
    http_queue.py
    mpfb_runtime.py
    ui.py
```
