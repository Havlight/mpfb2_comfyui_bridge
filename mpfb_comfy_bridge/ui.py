import hashlib
import json
import time
import uuid

import bpy

from . import capture
from . import http_queue
from . import local_save
from . import mpfb_runtime


_AUTO_TIMER_REGISTERED = False
_RESULT_TIMER_REGISTERED = False
_PANEL_REGISTERED = False
_LAST_AUTO_HASH = ""
_LAST_AUTO_ERROR = ""


def operations_category():
    try:
        UiService = mpfb_runtime.services().UiService
        return UiService.get_value("OPERATIONSCATEGORY")
    except Exception:
        return "MPFB"


def register_timers():
    global _RESULT_TIMER_REGISTERED
    if not _RESULT_TIMER_REGISTERED and not bpy.app.timers.is_registered(_flush_results_timer):
        bpy.app.timers.register(_flush_results_timer, first_interval=0.25)
        _RESULT_TIMER_REGISTERED = True


def unregister_timers():
    global _AUTO_TIMER_REGISTERED, _RESULT_TIMER_REGISTERED
    for timer in (_auto_sync_timer, _flush_results_timer, _register_panel_timer):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    _AUTO_TIMER_REGISTERED = False
    _RESULT_TIMER_REGISTERED = False


def _camera_poll(_self, obj):
    return obj is not None and obj.type == "CAMERA"


def _auto_sync_changed(self, _context):
    if self.auto_pose_sync:
        _ensure_auto_timer()


class MPFBComfyBridgeProperties(bpy.types.PropertyGroup):
    comfy_url: bpy.props.StringProperty(
        name="ComfyUI URL",
        default="http://127.0.0.1:8188",
    )
    token: bpy.props.StringProperty(
        name="Token",
        default="",
        subtype="PASSWORD",
    )
    source_id: bpy.props.StringProperty(
        name="Source ID",
        default="blender",
    )
    target_bridge_id: bpy.props.StringProperty(
        name="Target Bridge ID",
        default="",
    )
    lock_target_id: bpy.props.BoolProperty(
        name="Lock Target ID",
        default=False,
    )
    camera: bpy.props.PointerProperty(
        name="Camera Override",
        type=bpy.types.Object,
        poll=_camera_poll,
    )
    include_hands: bpy.props.BoolProperty(
        name="Hands",
        default=True,
    )
    auto_pose_sync: bpy.props.BoolProperty(
        name="Auto Pose Sync",
        default=False,
        update=_auto_sync_changed,
    )
    auto_interval: bpy.props.FloatProperty(
        name="Interval",
        default=0.5,
        min=0.2,
        max=10.0,
        subtype="TIME",
    )
    pause_if_editor_open: bpy.props.BoolProperty(
        name="Pause when Studio editor is open",
        default=True,
    )
    save_local_after_send: bpy.props.BoolProperty(
        name="Save local after send",
        default=False,
    )
    local_save_dir: bpy.props.StringProperty(
        name="Save Folder",
        default=local_save.DEFAULT_SAVE_DIR,
        subtype="DIR_PATH",
    )
    status: bpy.props.StringProperty(
        name="Status",
        default="Idle",
    )
    last_error: bpy.props.StringProperty(
        name="Last Error",
        default="",
    )
    last_sent: bpy.props.StringProperty(
        name="Last Sent",
        default="",
    )


class MPFBCOMFYBRIDGE_OT_CheckConnection(bpy.types.Operator):
    bl_idname = "mpfb_comfy_bridge.check_connection"
    bl_label = "Check Connection"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.mpfb_comfy_bridge
        props.status = "Checking ComfyUI..."
        props.last_error = ""
        http_queue.enqueue_status(props.comfy_url, props.token)
        return {"FINISHED"}


class _SendBase:
    def _props(self, context):
        return context.scene.mpfb_comfy_bridge

    def _camera(self, context, props):
        return capture.resolve_camera(context, props.camera)

    def _metadata(self, context, props, camera, batch_id=None, expected=None, auto_sync=False):
        target_id = props.target_bridge_id.strip() if props.lock_target_id else ""
        width, height = capture.effective_resolution(context.scene)
        metadata = {
            "target_bridge_id": target_id,
            "batch_id": batch_id,
            "batch_expected_channels": expected or [],
            "render_resolution": [width, height],
            "camera_name": camera.name,
            "frame": context.scene.frame_current,
            "auto_sync": bool(auto_sync),
            "pause_if_editor_open": bool(props.pause_if_editor_open),
            "render_border_disabled": True,
        }
        return metadata

    def _queue_pose(self, context, props, camera, batch_id=None, expected=None, auto_sync=False, timestamp=None):
        pose, pose_meta = capture.capture_pose_payload(context, camera, include_hands=props.include_hands)
        metadata = self._metadata(context, props, camera, batch_id, expected, auto_sync)
        metadata.update(pose_meta)
        if not auto_sync:
            save_warning = self._save_pose_local(props, pose, timestamp)
            if save_warning:
                metadata["local_save_warning"] = save_warning
        payload = {
            "source_id": props.source_id.strip() or "blender",
            "pose_json": pose,
            "metadata": metadata,
        }
        http_queue.enqueue_pose(
            props.comfy_url,
            props.token,
            payload,
            "Auto pose sync" if auto_sync else "Send pose",
        )
        return pose, metadata

    def _queue_image(self, props, camera, channel, image_bytes, metadata, timestamp=None):
        metadata = dict(metadata)
        metadata["camera_name"] = camera.name
        save_warning = self._save_image_local(props, timestamp, channel, image_bytes)
        if save_warning:
            metadata["local_save_warning"] = save_warning
        http_queue.enqueue_image(
            props.comfy_url,
            props.token,
            props.source_id.strip() or "blender",
            channel,
            image_bytes,
            metadata,
            f"Send {channel}",
        )
        return save_warning

    def _save_pose_local(self, props, pose, timestamp=None):
        if not props.save_local_after_send:
            return ""
        try:
            local_save.write_pose_json(props.local_save_dir, timestamp or local_save.make_timestamp(), pose)
            return ""
        except Exception as exc:
            return f"Local save failed: {exc}"

    def _save_image_local(self, props, timestamp, channel, image_bytes):
        if not props.save_local_after_send:
            return ""
        try:
            local_save.write_png(props.local_save_dir, timestamp or local_save.make_timestamp(), channel, image_bytes)
            return ""
        except Exception as exc:
            return f"Local save failed: {exc}"

    def _set_warning(self, props, warning):
        props.last_error = warning or ""
        if warning:
            self.report({"WARNING"}, warning)

    def _warning_from_metadata(self, metadata):
        warnings = []
        projection = metadata.get("projection_warning", "")
        local_warning = metadata.get("local_save_warning", "")
        if projection:
            warnings.append(projection)
        if local_warning:
            warnings.append(local_warning)
        return " | ".join(warnings)


class MPFBCOMFYBRIDGE_OT_SendPose(_SendBase, bpy.types.Operator):
    bl_idname = "mpfb_comfy_bridge.send_pose"
    bl_label = "Send Pose"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = self._props(context)
        try:
            camera = self._camera(context, props)
            with capture.RenderBorderGuard(context.scene):
                _pose, metadata = self._queue_pose(context, props, camera)
            props.status = "Pose queued"
            props.last_sent = _stamp("pose")
            self._set_warning(props, self._warning_from_metadata(metadata))
        except Exception as exc:
            props.status = "Pose failed"
            props.last_error = str(exc)
            self.report({"ERROR"}, str(exc))
        return {"FINISHED"}


class MPFBCOMFYBRIDGE_OT_SendBeauty(_SendBase, bpy.types.Operator):
    bl_idname = "mpfb_comfy_bridge.send_beauty"
    bl_label = "Send Beauty"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = self._props(context)
        try:
            camera = self._camera(context, props)
            with capture.RenderBorderGuard(context.scene):
                metadata = self._metadata(context, props, camera, expected=["beauty"])
                image = capture.capture_beauty_png(context, camera)
            save_warning = self._queue_image(props, camera, "beauty", image, metadata)
            props.status = "Beauty queued"
            props.last_sent = _stamp("beauty")
            self._set_warning(props, save_warning)
        except Exception as exc:
            props.status = "Beauty failed"
            props.last_error = str(exc)
            self.report({"ERROR"}, str(exc))
        return {"FINISHED"}


class MPFBCOMFYBRIDGE_OT_SendDepth(_SendBase, bpy.types.Operator):
    bl_idname = "mpfb_comfy_bridge.send_depth"
    bl_label = "Send Depth"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = self._props(context)
        try:
            camera = self._camera(context, props)
            with capture.RenderBorderGuard(context.scene):
                metadata = self._metadata(context, props, camera, expected=["depth"])
                image, depth_meta = capture.capture_depth_png(context, camera)
                metadata.update(depth_meta)
            save_warning = self._queue_image(props, camera, "depth", image, metadata)
            props.status = "Depth queued"
            props.last_sent = _stamp("depth")
            self._set_warning(props, save_warning)
        except Exception as exc:
            props.status = "Depth failed"
            props.last_error = str(exc)
            self.report({"ERROR"}, str(exc))
        return {"FINISHED"}


class MPFBCOMFYBRIDGE_OT_SendAll(_SendBase, bpy.types.Operator):
    bl_idname = "mpfb_comfy_bridge.send_all"
    bl_label = "Send All"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = self._props(context)
        batch_id = uuid.uuid4().hex
        expected = ["pose", "beauty", "depth"]
        try:
            camera = self._camera(context, props)
            save_timestamp = local_save.make_timestamp()
            with capture.RenderBorderGuard(context.scene):
                _pose, pose_metadata = self._queue_pose(context, props, camera, batch_id, expected, False, save_timestamp)

                beauty_metadata = self._metadata(context, props, camera, batch_id, expected)
                beauty = capture.capture_beauty_png(context, camera)
                beauty_warning = self._queue_image(props, camera, "beauty", beauty, beauty_metadata, save_timestamp)

                depth_metadata = self._metadata(context, props, camera, batch_id, expected)
                depth, depth_meta = capture.capture_depth_png(context, camera)
                depth_metadata.update(depth_meta)
                depth_warning = self._queue_image(props, camera, "depth", depth, depth_metadata, save_timestamp)
            props.status = "Pose, beauty and depth queued"
            props.last_sent = _stamp("all")
            warnings = [self._warning_from_metadata(pose_metadata), beauty_warning, depth_warning]
            self._set_warning(props, " | ".join([warning for warning in warnings if warning]))
        except Exception as exc:
            props.status = "Send all failed"
            props.last_error = str(exc)
            self.report({"ERROR"}, str(exc))
        return {"FINISHED"}


class MPFBCOMFYBRIDGE_PT_Panel(bpy.types.Panel):
    bl_idname = "MPFBCOMFYBRIDGE_PT_Panel"
    bl_label = "ComfyUI Bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MPFB"
    bl_parent_id = "MPFB_PT_Ai_Panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        props = context.scene.mpfb_comfy_bridge
        layout = self.layout

        conn = layout.box()
        conn.label(text="Connection")
        conn.prop(props, "comfy_url")
        conn.prop(props, "token")
        conn.operator("mpfb_comfy_bridge.check_connection", icon="URL")
        conn.label(text=props.status)
        if props.last_error:
            conn.label(text=props.last_error, icon="ERROR")

        target = layout.box()
        target.label(text="Target")
        target.prop(props, "source_id")
        target.prop(props, "target_bridge_id")
        target.prop(props, "lock_target_id")
        target.prop(props, "camera")
        target.prop(props, "include_hands")

        send = layout.box()
        send.label(text="Send")
        row = send.row(align=True)
        row.operator("mpfb_comfy_bridge.send_pose", icon="ARMATURE_DATA")
        row.operator("mpfb_comfy_bridge.send_beauty", icon="RENDER_RESULT")
        row = send.row(align=True)
        row.operator("mpfb_comfy_bridge.send_depth", icon="IMAGE_DATA")
        row.operator("mpfb_comfy_bridge.send_all", icon="EXPORT")
        if props.last_sent:
            send.label(text=props.last_sent)

        local = layout.box()
        local.label(text="Local Save")
        local.prop(props, "save_local_after_send")
        local.prop(props, "local_save_dir")

        auto = layout.box()
        auto.label(text="Auto Sync")
        auto.prop(props, "auto_pose_sync")
        auto.prop(props, "auto_interval")
        auto.prop(props, "pause_if_editor_open")
        auto.label(text="Auto sync overwrites Studio edits.", icon="ERROR")


CLASSES = (
    MPFBComfyBridgeProperties,
    MPFBCOMFYBRIDGE_OT_CheckConnection,
    MPFBCOMFYBRIDGE_OT_SendPose,
    MPFBCOMFYBRIDGE_OT_SendBeauty,
    MPFBCOMFYBRIDGE_OT_SendDepth,
    MPFBCOMFYBRIDGE_OT_SendAll,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mpfb_comfy_bridge = bpy.props.PointerProperty(type=MPFBComfyBridgeProperties)
    if not _try_register_panel():
        bpy.app.timers.register(_register_panel_timer, first_interval=0.5)
    register_timers()
    http_queue.start_worker()


def unregister():
    unregister_timers()
    http_queue.stop_worker()
    if hasattr(bpy.types.Scene, "mpfb_comfy_bridge"):
        del bpy.types.Scene.mpfb_comfy_bridge
    _unregister_panel()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


def _try_register_panel():
    global _PANEL_REGISTERED
    if _PANEL_REGISTERED:
        return True
    if not hasattr(bpy.types, MPFBCOMFYBRIDGE_PT_Panel.bl_parent_id):
        return False
    MPFBCOMFYBRIDGE_PT_Panel.bl_category = operations_category()
    bpy.utils.register_class(MPFBCOMFYBRIDGE_PT_Panel)
    _PANEL_REGISTERED = True
    return True


def _unregister_panel():
    global _PANEL_REGISTERED
    if not _PANEL_REGISTERED:
        return
    bpy.utils.unregister_class(MPFBCOMFYBRIDGE_PT_Panel)
    _PANEL_REGISTERED = False


def _register_panel_timer():
    if _try_register_panel():
        return None
    return 1.0


def _ensure_auto_timer():
    global _AUTO_TIMER_REGISTERED
    if not _AUTO_TIMER_REGISTERED and not bpy.app.timers.is_registered(_auto_sync_timer):
        bpy.app.timers.register(_auto_sync_timer, first_interval=0.2)
        _AUTO_TIMER_REGISTERED = True


def _auto_sync_timer():
    global _AUTO_TIMER_REGISTERED, _LAST_AUTO_HASH, _LAST_AUTO_ERROR
    scene = bpy.context.scene
    props = getattr(scene, "mpfb_comfy_bridge", None)
    if props is None or not props.auto_pose_sync:
        _AUTO_TIMER_REGISTERED = False
        return None

    try:
        camera = capture.resolve_camera(bpy.context, props.camera)
        sender = _SendBase()
        with capture.RenderBorderGuard(scene):
            pose, pose_meta = capture.capture_pose_payload(bpy.context, camera, include_hands=props.include_hands)
        digest = hashlib.sha256(json.dumps(pose, sort_keys=True).encode("utf-8")).hexdigest()
        if digest == _LAST_AUTO_HASH:
            return max(0.2, props.auto_interval)
        _LAST_AUTO_HASH = digest
        metadata = sender._metadata(bpy.context, props, camera, auto_sync=True)
        metadata.update(pose_meta)
        payload = {
            "source_id": props.source_id.strip() or "blender",
            "pose_json": pose,
            "metadata": metadata,
        }
        http_queue.enqueue_pose(props.comfy_url, props.token, payload, "Auto pose sync")
        props.status = "Auto pose queued"
        props.last_sent = _stamp("auto pose")
        props.last_error = metadata.get("projection_warning", "")
        _LAST_AUTO_ERROR = ""
    except Exception as exc:
        if str(exc) != _LAST_AUTO_ERROR:
            props.status = "Auto sync waiting"
            props.last_error = str(exc)
            _LAST_AUTO_ERROR = str(exc)
    return max(0.2, props.auto_interval)


def _flush_results_timer():
    scene = bpy.context.scene
    props = getattr(scene, "mpfb_comfy_bridge", None)
    queue = http_queue.result_queue()
    while not queue.empty():
        result = queue.get()
        if props is None:
            continue
        label = result.get("job", {}).get("label", "Bridge job")
        if result.get("ok"):
            props.status = f"{label} OK"
            props.last_error = ""
        else:
            props.status = f"{label} failed"
            props.last_error = result.get("error", "Unknown error")
    return 0.5


def _stamp(label):
    return f"Last queued {label} at {time.strftime('%H:%M:%S')}"
