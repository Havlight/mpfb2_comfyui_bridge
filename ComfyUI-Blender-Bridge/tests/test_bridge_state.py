import importlib.util
import json
import os


def load_bridge_state():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "bridge_state.py")
    spec = importlib.util.spec_from_file_location("bridge_state_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\x82\x81\x89"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_save_pose_and_fingerprint(tmp_path, monkeypatch):
    state = load_bridge_state()
    monkeypatch.setattr(state, "bridge_root", lambda: str(tmp_path / "blender_bridge"))

    pose = {
        "canvas_width": 512,
        "canvas_height": 512,
        "people": [{"pose_keypoints_2d": [1, 2, 1] * 18}],
    }
    meta = state.save_pose(
        "blender",
        pose,
        {
            "batch_id": "batch-1",
            "batch_expected_channels": ["pose", "beauty", "depth"],
            "target_bridge_id": "target-1",
        },
    )

    assert meta["update_id"] == 1
    assert meta["target_bridge_id"] == "target-1"
    assert state.get_latest("blender", "pose")["pose_json"]["canvas_width"] == 512
    assert "target-1" in state.fingerprint("blender", "pose", False, "target-1")
    assert state.is_complete_batch(meta) is False


def test_save_image_sidecar_and_batch_completion(tmp_path, monkeypatch):
    state = load_bridge_state()
    monkeypatch.setattr(state, "bridge_root", lambda: str(tmp_path / "blender_bridge"))

    expected = ["pose", "beauty", "depth"]
    pose = {
        "canvas_width": 1,
        "canvas_height": 1,
        "people": [{"pose_keypoints_2d": [1, 1, 1] * 18}],
    }
    pose_meta = state.save_pose("blender", pose, {"batch_id": "batch-2", "batch_expected_channels": expected})
    beauty_meta = state.save_image("blender", "beauty", PNG_1X1, {"batch_id": "batch-2", "batch_expected_channels": expected})
    assert state.is_complete_batch(beauty_meta) is False

    depth_meta = state.save_image("blender", "depth", PNG_1X1, {"batch_id": "batch-2", "batch_expected_channels": expected})
    assert state.is_complete_batch(depth_meta) is True

    sidecar = tmp_path / "blender_bridge" / "blender" / "depth.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["width"] == 1
    assert os.path.isfile(state.image_abs_path(depth_meta))
    assert state.image_abs_path(depth_meta).endswith(os.path.join("blender", "depth.png"))
    assert state.is_complete_batch(pose_meta) is True


def test_public_metadata_is_preserved(tmp_path, monkeypatch):
    state = load_bridge_state()
    monkeypatch.setattr(state, "bridge_root", lambda: str(tmp_path / "blender_bridge"))

    pose = {
        "canvas_width": 512,
        "canvas_height": 512,
        "people": [{"pose_keypoints_2d": [1, 2, 1] * 18}],
    }
    meta = state.save_pose(
        "blender",
        pose,
        {
            "auto_sync": True,
            "pause_if_editor_open": True,
            "projection_warning": "1 keypoint outside the render canvas",
            "camera_name": "Camera",
            "private_note": "not exposed",
        },
    )

    assert meta["auto_sync"] is True
    assert meta["pause_if_editor_open"] is True
    assert meta["projection_warning"] == "1 keypoint outside the render canvas"
    assert meta["camera_name"] == "Camera"
    assert "private_note" not in meta


def test_reject_unsafe_source_id(tmp_path, monkeypatch):
    state = load_bridge_state()
    monkeypatch.setattr(state, "bridge_root", lambda: str(tmp_path / "blender_bridge"))

    try:
        state.save_image("../bad", "beauty", PNG_1X1, {})
    except ValueError as exc:
        assert "source_id" in str(exc)
    else:
        raise AssertionError("unsafe source id was accepted")
