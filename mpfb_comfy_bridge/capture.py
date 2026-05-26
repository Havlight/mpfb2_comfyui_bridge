import os
import tempfile

import bpy
import bmesh
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from . import mpfb_runtime


def effective_resolution(scene):
    scale = max(1, int(scene.render.resolution_percentage)) / 100.0
    width = max(1, int(round(scene.render.resolution_x * scale)))
    height = max(1, int(round(scene.render.resolution_y * scale)))
    return width, height


def selected_armatures(context):
    return [obj for obj in context.selected_objects if obj.type == "ARMATURE"]


def resolve_camera(context, override_camera=None):
    camera = override_camera or context.scene.camera
    if camera is None or camera.type != "CAMERA":
        raise ValueError("No scene camera is selected")
    return camera


class RenderBorderGuard:
    def __init__(self, scene):
        self.scene = scene
        render = scene.render
        self.values = {}
        for name in ("use_border", "use_crop_to_border"):
            if hasattr(render, name):
                self.values[name] = getattr(render, name)

    def __enter__(self):
        render = self.scene.render
        for name in self.values:
            setattr(render, name, False)
        return self

    def __exit__(self, exc_type, exc, tb):
        render = self.scene.render
        for name, value in self.values.items():
            setattr(render, name, value)


class CameraGuard:
    def __init__(self, scene, camera):
        self.scene = scene
        self.camera = camera
        self.old_camera = scene.camera

    def __enter__(self):
        self.scene.camera = self.camera

    def __exit__(self, exc_type, exc, tb):
        self.scene.camera = self.old_camera


class RenderOutputGuard:
    def __init__(self, scene, filepath):
        self.scene = scene
        self.filepath = filepath
        self.render = scene.render
        self.image_settings = self.render.image_settings
        self.old = {
            "filepath": self.render.filepath,
            "file_format": self.image_settings.file_format,
            "color_mode": self.image_settings.color_mode,
            "color_depth": self.image_settings.color_depth,
        }

    def __enter__(self):
        self.render.filepath = self.filepath
        self.image_settings.file_format = "PNG"
        self.image_settings.color_mode = "RGBA"
        self.image_settings.color_depth = "8"

    def __exit__(self, exc_type, exc, tb):
        self.render.filepath = self.old["filepath"]
        self.image_settings.file_format = self.old["file_format"]
        self.image_settings.color_mode = self.old["color_mode"]
        self.image_settings.color_depth = self.old["color_depth"]


def capture_pose_payload(context, camera, include_hands=True):
    ObjectService, RigService, constants, AI_PROPERTIES = _mpfb_runtime()
    width, height = effective_resolution(context.scene)
    armatures = selected_armatures(context)
    if not armatures:
        raise ValueError("Select at least one MPFB armature")

    _validate_armatures(armatures, ObjectService, RigService)
    confidence = _confidence_values(context.scene, AI_PROPERTIES)
    people = []
    projection_warnings = []

    depsgraph = context.evaluated_depsgraph_get()
    for person_id, armature in enumerate(armatures):
        basemesh = ObjectService.find_object_of_type_amongst_nearest_relatives(armature, "Basemesh")
        if not basemesh:
            raise ValueError("Could not find a Basemesh object for one armature")
        bm = bmesh.new()
        try:
            bm.from_object(basemesh, depsgraph)
            bm.verts.ensure_lookup_table()
            body, warnings = _keypoints_for_mapper(
                armature,
                bm,
                context.scene,
                camera,
                width,
                height,
                constants.COCO,
                confidence,
                ObjectService,
                RigService,
            )
            projection_warnings.extend(warnings)
            person = {
                "person_id": person_id,
                "pose_keypoints_2d": body,
                "face_keypoints_2d": [],
                "hand_left_keypoints_2d": [],
                "hand_right_keypoints_2d": [],
            }
            if include_hands:
                left, left_warnings = _keypoints_for_mapper(
                    armature,
                    bm,
                    context.scene,
                    camera,
                    width,
                    height,
                    constants.LEFT_HAND,
                    confidence,
                    ObjectService,
                    RigService,
                )
                right, right_warnings = _keypoints_for_mapper(
                    armature,
                    bm,
                    context.scene,
                    camera,
                    width,
                    height,
                    constants.RIGHT_HAND,
                    confidence,
                    ObjectService,
                    RigService,
                )
                projection_warnings.extend(left_warnings)
                projection_warnings.extend(right_warnings)
                person["hand_left_keypoints_2d"] = left
                person["hand_right_keypoints_2d"] = right
            people.append(person)
        finally:
            bm.free()

    payload = {
        "version": "1.3",
        "canvas_width": width,
        "canvas_height": height,
        "people": people,
    }
    warning = ""
    if projection_warnings:
        warning = f"{len(projection_warnings)} keypoints projected outside the camera canvas"
    return payload, {
        "pose_resolution": [width, height],
        "camera_name": camera.name,
        "frame": context.scene.frame_current,
        "projection_mode": "CAMERA",
        "projection_warning": warning,
        "render_border_disabled": True,
    }


def capture_beauty_png(context, camera):
    path = _temp_png_path("beauty")
    try:
        with CameraGuard(context.scene, camera), RenderOutputGuard(context.scene, path):
            bpy.ops.render.render(write_still=True)
        return _read_and_remove(path)
    finally:
        _remove_if_exists(path)


def capture_depth_png(context, camera):
    scene = context.scene
    view_layer = context.view_layer
    old_use_nodes = scene.use_nodes
    old_use_pass_z = view_layer.use_pass_z
    temp_nodes = []
    temp_links = []
    depth_image = None
    path = _temp_png_path("depth")
    try:
        scene.use_nodes = True
        view_layer.use_pass_z = True
        tree = scene.node_tree
        if tree is None:
            raise ValueError("Scene compositor node tree is unavailable")

        render_layers = tree.nodes.new(type="CompositorNodeRLayers")
        normalize = tree.nodes.new(type="CompositorNodeNormalize")
        viewer = tree.nodes.new(type="CompositorNodeViewer")
        temp_nodes.extend([render_layers, normalize, viewer])
        render_layers.label = "MPFB Bridge Render Layers"
        normalize.label = "MPFB Bridge Normalize Depth"
        viewer.label = "MPFB Bridge Depth Viewer"

        depth_socket = render_layers.outputs.get("Depth") or render_layers.outputs.get("Z")
        if depth_socket is None:
            raise ValueError("Render Layers node has no depth output")
        temp_links.append(tree.links.new(depth_socket, normalize.inputs[0]))
        temp_links.append(tree.links.new(normalize.outputs[0], viewer.inputs[0]))

        with CameraGuard(scene, camera):
            bpy.ops.render.render(write_still=False)

        viewer_image = bpy.data.images.get("Viewer Node")
        if viewer_image is None or viewer_image.size[0] <= 0 or viewer_image.size[1] <= 0:
            raise ValueError("Depth viewer image was not generated")

        width, height = viewer_image.size
        pixels = [0.0] * (width * height * 4)
        viewer_image.pixels.foreach_get(pixels)
        depth_image = bpy.data.images.new("MPFB Comfy Bridge Depth", width, height, alpha=True)
        depth_image.pixels.foreach_set(pixels)
        depth_image.filepath_raw = path
        depth_image.file_format = "PNG"
        depth_image.save()
        return _read_and_remove(path), {
            "render_resolution": [width, height],
            "depth_normalization": {"mode": "compositor_normalize"},
        }
    finally:
        if depth_image is not None:
            bpy.data.images.remove(depth_image)
        if scene.node_tree is not None:
            for link in temp_links:
                try:
                    scene.node_tree.links.remove(link)
                except Exception:
                    pass
            for node in temp_nodes:
                try:
                    scene.node_tree.nodes.remove(node)
                except Exception:
                    pass
        view_layer.use_pass_z = old_use_pass_z
        scene.use_nodes = old_use_nodes
        _remove_if_exists(path)


def _mpfb_runtime():
    services = mpfb_runtime.services()
    ObjectService = services.ObjectService
    RigService = services.RigService
    constants = mpfb_runtime.openpose_constants()
    try:
        AI_PROPERTIES = mpfb_runtime.ai_panel().AI_PROPERTIES
    except Exception:
        AI_PROPERTIES = None
    return ObjectService, RigService, constants, AI_PROPERTIES


def _validate_armatures(armatures, ObjectService, RigService):
    for armature in armatures:
        rig_type = RigService.identify_rig(armature)
        if not rig_type or "default" not in rig_type:
            raise ValueError("Only MPFB default rigs are supported")
        basemesh = ObjectService.find_object_of_type_amongst_nearest_relatives(armature, "Basemesh")
        if not basemesh:
            raise ValueError("Could not find a basemesh for one of the armatures")
        for modifier in basemesh.modifiers:
            if modifier.type == "MASK" and modifier.vertex_group == "body" and modifier.invert_vertex_group:
                raise ValueError("The base mesh has a mask modifier hiding the body")


def _confidence_values(scene, AI_PROPERTIES):
    defaults = {"LOW": 0.1, "MEDIUM": 0.6, "HIGH": 1.0}
    if AI_PROPERTIES is None:
        return defaults
    try:
        return {
            "LOW": AI_PROPERTIES.get_value("lowconfidence", entity_reference=scene),
            "MEDIUM": AI_PROPERTIES.get_value("mediumconfidence", entity_reference=scene),
            "HIGH": AI_PROPERTIES.get_value("highconfidence", entity_reference=scene),
        }
    except Exception:
        return defaults


def _keypoints_for_mapper(armature, bm, scene, camera, width, height, mapper, confidence, ObjectService, RigService):
    coord_mapping = []
    warnings = []
    for position in mapper:
        keypoint = _world_keypoint(armature, bm, position, ObjectService, RigService)
        projected, warning = _project_to_camera(scene, camera, width, height, keypoint)
        if warning:
            warnings.append(warning)
        coord_mapping.append(projected[0])
        coord_mapping.append(projected[1])
        coord_mapping.append(float(confidence.get(position.get("confidence"), 0.1)))
    return coord_mapping, warnings


def _world_keypoint(armature, bm, position, ObjectService, RigService):
    if position["type"] == "vertex":
        return armature.matrix_world @ bm.verts[position["data"]].co
    if position["type"] == "mean":
        keypoint = Vector((0.0, 0.0, 0.0))
        for index in position["data"]:
            keypoint += armature.matrix_world @ bm.verts[index].co
        return keypoint / len(position["data"])
    if position["type"] in ("head", "tail"):
        loc = RigService.get_world_space_location_of_pose_bone(position["data"], armature)
        return Vector(loc[position["type"]])
    raise ValueError("Unsupported OpenPose mapper entry")


def _project_to_camera(scene, camera, width, height, keypoint):
    cam_coord = world_to_camera_view(scene, camera, Vector(keypoint))
    x = float(cam_coord.x) * width
    y = (1.0 - float(cam_coord.y)) * height
    outside = cam_coord.x < 0.0 or cam_coord.x > 1.0 or cam_coord.y < 0.0 or cam_coord.y > 1.0 or cam_coord.z < 0.0
    return [x, y], "outside" if outside else ""


def _temp_png_path(prefix):
    handle = tempfile.NamedTemporaryFile(prefix=f"mpfb_comfy_{prefix}_", suffix=".png", delete=False)
    path = handle.name
    handle.close()
    return path


def _read_and_remove(path):
    with open(path, "rb") as handle:
        data = handle.read()
    _remove_if_exists(path)
    return data


def _remove_if_exists(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
