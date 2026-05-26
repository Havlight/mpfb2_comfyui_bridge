import json
import queue
import threading
import time
import uuid
import urllib.error
import urllib.request


_JOBS = queue.Queue()
_RESULTS = queue.Queue()
_STOP = threading.Event()
_WORKER = None


def start_worker():
    global _WORKER
    if _WORKER and _WORKER.is_alive():
        return
    _STOP.clear()
    _WORKER = threading.Thread(target=_worker_loop, name="MPFB Comfy Bridge HTTP", daemon=True)
    _WORKER.start()


def stop_worker():
    _STOP.set()
    _JOBS.put({"kind": "stop"})


def result_queue():
    return _RESULTS


def enqueue_status(base_url, token=""):
    _JOBS.put({
        "id": _job_id(),
        "kind": "status",
        "base_url": base_url,
        "token": token,
        "label": "Check connection",
    })


def enqueue_pose(base_url, token, payload, label="Send pose"):
    _JOBS.put({
        "id": _job_id(),
        "kind": "json",
        "base_url": base_url,
        "path": "/blender_bridge/pose",
        "token": token,
        "payload": payload,
        "label": label,
    })


def enqueue_image(base_url, token, source_id, channel, image_bytes, metadata=None, label="Send image"):
    _JOBS.put({
        "id": _job_id(),
        "kind": "image",
        "base_url": base_url,
        "path": "/blender_bridge/image",
        "token": token,
        "source_id": source_id,
        "channel": channel,
        "image_bytes": image_bytes,
        "metadata": metadata or {},
        "label": label,
    })


def _job_id():
    return uuid.uuid4().hex


def _url(base_url, path):
    return str(base_url or "").rstrip("/") + path


def _headers(token="", content_type="application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["X-Blender-Bridge-Token"] = token
    return headers


def _post_json(job):
    data = json.dumps(job["payload"]).encode("utf-8")
    request = urllib.request.Request(
        _url(job["base_url"], job["path"]),
        data=data,
        headers=_headers(job.get("token", ""), "application/json"),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return _read_response(response)


def _get_status(job):
    request = urllib.request.Request(
        _url(job["base_url"], "/blender_bridge/status"),
        headers=_headers(job.get("token", ""), "application/json"),
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return _read_response(response)


def _post_image(job):
    boundary = "----mpfb-comfy-bridge-" + uuid.uuid4().hex
    body = _multipart_body(
        boundary,
        {
            "source_id": job["source_id"],
            "channel": job["channel"],
            "metadata": json.dumps(job.get("metadata") or {}, sort_keys=True),
        },
        "image",
        f"{job['channel']}.png",
        job["image_bytes"],
    )
    request = urllib.request.Request(
        _url(job["base_url"], job["path"]),
        data=body,
        headers=_headers(job.get("token", ""), f"multipart/form-data; boundary={boundary}"),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return _read_response(response)


def _multipart_body(boundary, fields, file_field, filename, data):
    lines = []
    for key, value in fields.items():
        lines.extend([
            f"--{boundary}".encode("utf-8"),
            f'Content-Disposition: form-data; name="{key}"'.encode("utf-8"),
            b"",
            str(value).encode("utf-8"),
        ])
    lines.extend([
        f"--{boundary}".encode("utf-8"),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode("utf-8"),
        b"Content-Type: image/png",
        b"",
        data,
        f"--{boundary}--".encode("utf-8"),
        b"",
    ])
    return b"\r\n".join(lines)


def _read_response(response):
    raw = response.read()
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", errors="replace")}


def _worker_loop():
    while not _STOP.is_set():
        job = _JOBS.get()
        if job.get("kind") == "stop":
            return
        started = time.time()
        try:
            if job["kind"] == "status":
                payload = _get_status(job)
            elif job["kind"] == "json":
                payload = _post_json(job)
            elif job["kind"] == "image":
                payload = _post_image(job)
            else:
                raise ValueError("Unknown job kind")
            _RESULTS.put({
                "ok": True,
                "job": job,
                "payload": payload,
                "elapsed": time.time() - started,
            })
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            _RESULTS.put({"ok": False, "job": job, "error": f"HTTP {exc.code}: {detail}"})
        except Exception as exc:
            _RESULTS.put({"ok": False, "job": job, "error": str(exc)})
