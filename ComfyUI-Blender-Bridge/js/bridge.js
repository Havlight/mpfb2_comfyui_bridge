import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";


const ACTIVE_TARGET_KEY = "blender_bridge.active_target_id";
const SETUP_FLAG = "__blenderBridgeEventListenersInstalled";
const OPENPOSE_PATCH_FLAG = "__blenderBridgeOpenPosePatched";
const IMAGE_PATCH_FLAG = "__blenderBridgeImageReceiverPatched";


function bridgeApi() {
	return app?.api || api;
}


function bridgeUrl(path) {
	const apiObj = bridgeApi();
	const normalized = path.startsWith("/") ? path : `/${path}`;
	if (apiObj && typeof apiObj.apiURL === "function") {
		return apiObj.apiURL(normalized);
	}
	return normalized;
}


function showToast(severity, summary, detail, life = 4000) {
	const toast = app?.extensionManager?.toast;
	if (toast && typeof toast.add === "function") {
		toast.add({ severity, summary, detail, life });
		return;
	}
	const fn = severity === "error" ? "error" : severity === "warn" ? "warn" : "log";
	console[fn](`[ComfyUI-Blender-Bridge] ${summary}: ${detail}`);
}


function graphNodes() {
	return Array.isArray(app?.graph?._nodes) ? app.graph._nodes : [];
}


function comfyClass(node) {
	return node?.comfyClass || node?.type || "";
}


function normalizeClassName(value) {
	return String(value || "").replace(/[^A-Za-z0-9]/g, "").toLowerCase();
}


function isOpenPoseClassName(value) {
	return normalizeClassName(value) === "openposestudio";
}


function isImageReceiverClassName(value) {
	return normalizeClassName(value) === "blenderbridgeimagereceiver";
}


function isOpenPoseStudioNode(node) {
	return isOpenPoseClassName(comfyClass(node));
}


function isImageReceiverNode(node) {
	return isImageReceiverClassName(comfyClass(node));
}


function eventPayload(event) {
	return event?.detail?.data || event?.detail || event?.data || {};
}


function generateTargetId() {
	if (globalThis.crypto?.randomUUID) {
		return globalThis.crypto.randomUUID();
	}
	return `bridge-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}


function ensureTargetId(node) {
	if (!node) {
		return "";
	}
	if (!node.properties) {
		node.properties = {};
	}
	if (!node.properties.bridge_target_id) {
		node.properties.bridge_target_id = generateTargetId();
	}
	return node.properties.bridge_target_id;
}


function copyText(text) {
	try {
		if (navigator.clipboard?.writeText) {
			navigator.clipboard.writeText(text);
			return true;
		}
		const textarea = document.createElement("textarea");
		textarea.value = text;
		textarea.style.position = "fixed";
		textarea.style.left = "-9999px";
		document.body.appendChild(textarea);
		textarea.select();
		document.execCommand("copy");
		textarea.remove();
		return true;
	} catch (_err) {
		return false;
	}
}


function getWidget(node, name) {
	if (!Array.isArray(node?.widgets)) {
		return null;
	}
	return node.widgets.find((widget) => widget.name === name) || null;
}


function widgetValue(node, name, fallback = "") {
	const value = getWidget(node, name)?.value;
	return value == null ? fallback : String(value);
}


function normalizedWidgetValue(node, name, fallback = "") {
	return widgetValue(node, name, fallback).trim();
}


function getSelectedOpenPoseNode() {
	const selected = globalThis.LiteGraph?.LGraphCanvas?.active_canvas?.selected_nodes;
	if (!selected) {
		return null;
	}
	const nodes = Array.isArray(selected) ? selected : Object.values(selected);
	return nodes.find(isOpenPoseStudioNode) || null;
}


function getOpenEditorNode() {
	return graphNodes().find((node) => isOpenPoseStudioNode(node) && node.openPosePanel) || null;
}


function findOpenPoseTarget(targetId) {
	const nodes = graphNodes().filter(isOpenPoseStudioNode);
	for (const node of nodes) {
		ensureTargetId(node);
	}
	if (targetId) {
		return nodes.find((node) => node.properties?.bridge_target_id === targetId) || null;
	}
	let activeTarget = "";
	try {
		activeTarget = globalThis.localStorage?.getItem(ACTIVE_TARGET_KEY) || "";
	} catch (_err) {
		activeTarget = "";
	}
	if (activeTarget) {
		const activeNode = nodes.find((node) => node.properties?.bridge_target_id === activeTarget);
		if (activeNode) {
			return activeNode;
		}
	}
	return getOpenEditorNode() || getSelectedOpenPoseNode() || nodes[0] || null;
}


function stringifyPose(poseJson) {
	if (typeof poseJson === "string") {
		return poseJson;
	}
	return JSON.stringify(poseJson || {});
}


function setNodePose(node, poseText) {
	if (!node.properties) {
		node.properties = {};
	}
	node.properties.savedPose = poseText;
	if (!node.jsonWidget && Array.isArray(node.widgets)) {
		node.jsonWidget = node.widgets.find((widget) => widget.name === "pose_json");
	}
	if (node.jsonWidget) {
		node.jsonWidget.value = poseText;
	}
	if (typeof node.updatePreview === "function") {
		node.updatePreview();
	}
	app?.graph?.setDirtyCanvas?.(true, true);
}


function updatePanelBaseline(panel, poseText) {
	let parsed = null;
	try {
		parsed = JSON.parse(poseText);
	} catch (_err) {
		parsed = null;
	}
	panel.originalPose = poseText;
	panel.originalPoseData = parsed;
	panel.workingPoseData = parsed ? JSON.parse(JSON.stringify(parsed)) : null;
}


async function updateOpenEditor(node, poseText) {
	const panel = node?.openPosePanel;
	if (!panel || typeof panel.loadJSON !== "function") {
		return { ok: false, reason: "OpenPose Studio editor is not open" };
	}
	try {
		const result = panel.loadJSON(poseText, "blender-bridge", { silent: true });
		const error = typeof result?.then === "function" ? await result : result;
		if (error === null || error === undefined) {
			updatePanelBaseline(panel, poseText);
			return { ok: true };
		}
		return { ok: false, reason: String(error) };
	} catch (err) {
		return { ok: false, reason: err?.message || String(err) };
	}
}


async function applyPoseEvent(payload) {
	const poseJson = payload?.pose_json;
	if (!poseJson) {
		return;
	}
	const targetId = payload.target_bridge_id || "";
	const node = findOpenPoseTarget(targetId);
	if (!node) {
		showToast("warn", "Blender Bridge", "Received pose, but no OpenPose Studio node was found.", 6000);
		return;
	}
	if (payload.auto_sync && payload.pause_if_editor_open && node.openPosePanel) {
		showToast("warn", "Blender Bridge", "Auto pose sync skipped because OpenPose Studio editor is open.", 5000);
		return;
	}

	ensureTargetId(node);
	const poseText = stringifyPose(poseJson);
	setNodePose(node, poseText);

	const editorResult = await updateOpenEditor(node, poseText);
	if (!editorResult.ok && node.openPosePanel) {
		showToast("warn", "Blender Bridge", `Pose updated on node, but editor reload failed: ${editorResult.reason}`, 7000);
	}

	const people = Array.isArray(poseJson.people) ? poseJson.people.length : 0;
	const canvas = `${poseJson.canvas_width || "?"}x${poseJson.canvas_height || "?"}`;
	showToast("success", "Blender Bridge", `Pose updated from ${payload.source_id || "Blender"} (${people} people, ${canvas}).`, 3000);
}


function imagePreviewUrl(payload) {
	const source = encodeURIComponent(payload.source_id || "blender");
	const channel = encodeURIComponent(payload.channel || "");
	const query = new URLSearchParams();
	if (payload.update_id != null) {
		query.set("update_id", String(payload.update_id));
	}
	if (payload.hash) {
		query.set("hash", String(payload.hash));
	}
	const suffix = query.toString();
	return bridgeUrl(`/blender_bridge/preview/${source}/${channel}.png${suffix ? `?${suffix}` : ""}`);
}


function setImagePreviewState(node, payload, status = "received") {
	const preview = node?.__blenderBridgeImagePreview;
	if (!preview) {
		return;
	}
	preview.payload = payload || {};
	if (status === "received") {
		const size = payload.width && payload.height ? `${payload.width}x${payload.height}` : "unknown size";
		const metaText = `${payload.channel || "image"} ${size} #${payload.update_id ?? "?"}`;
		if (preview.kind === "dom") {
			preview.img.style.display = "block";
			preview.placeholder.style.display = "none";
			preview.meta.textContent = metaText;
			preview.meta.title = payload.hash || "";
		} else {
			preview.status = "";
			preview.metaText = metaText;
		}
		preview.img.onload = () => {
			void drawPoseOverlay(preview, payload);
			app?.graph?.setDirtyCanvas?.(true, true);
		};
		preview.img.src = imagePreviewUrl(payload);
		if (preview.img.complete) {
			void drawPoseOverlay(preview, payload);
		}
		app?.graph?.setDirtyCanvas?.(true, true);
		return;
	}
	preview.img.src = "";
	if (preview.kind === "dom") {
		preview.img.style.display = "none";
		preview.placeholder.style.display = "flex";
		preview.placeholder.textContent = status;
		preview.meta.textContent = "";
	} else {
		preview.status = status;
		preview.metaText = "";
	}
	clearOverlay(preview);
	app?.graph?.setDirtyCanvas?.(true, true);
}


function ensureImagePreviewWidget(node) {
	if (!node || node.__blenderBridgeImagePreview) {
		return;
	}
	if (typeof node.addDOMWidget === "function") {
		ensureDomImagePreviewWidget(node);
	} else {
		ensureCanvasImagePreviewWidget(node);
	}
	patchImageReceiverWidgets(node);
	void refreshImagePreviewFromLatest(node);
}


function ensureDomImagePreviewWidget(node) {
	const container = document.createElement("div");
	container.className = "blender-bridge-image-preview";
	container.style.cssText = [
		"width:100%",
		"height:100%",
		"position:relative",
		"overflow:hidden",
		"box-sizing:border-box",
		"border:1px solid rgba(128,128,128,0.35)",
		"border-radius:4px",
		"background:rgba(0,0,0,0.18)",
	].join(";");

	const img = document.createElement("img");
	img.alt = "Blender Bridge preview";
	img.style.cssText = "width:100%;height:100%;object-fit:contain;display:none;";

	const placeholder = document.createElement("div");
	placeholder.textContent = "No image received";
	placeholder.style.cssText = [
		"position:absolute",
		"inset:0",
		"display:flex",
		"align-items:center",
		"justify-content:center",
		"color:#888",
		"font:12px Arial,sans-serif",
		"text-align:center",
		"padding:8px",
	].join(";");

	const meta = document.createElement("div");
	meta.style.cssText = [
		"position:absolute",
		"left:6px",
		"right:6px",
		"bottom:5px",
		"padding:2px 4px",
		"border-radius:3px",
		"background:rgba(0,0,0,0.55)",
		"color:#fff",
		"font:11px Arial,sans-serif",
		"white-space:nowrap",
		"overflow:hidden",
		"text-overflow:ellipsis",
	].join(";");

	const overlay = document.createElement("canvas");
	overlay.style.cssText = [
		"position:absolute",
		"inset:0",
		"width:100%",
		"height:100%",
		"pointer-events:none",
	].join(";");

	container.appendChild(img);
	container.appendChild(overlay);
	container.appendChild(placeholder);
	container.appendChild(meta);

	node.__blenderBridgeImagePreview = { kind: "dom", container, img, overlay, placeholder, meta, payload: {} };
	node.addDOMWidget("bridge_preview", "image", container, {
		computeSize: () => [220, 180],
		serialize: false,
	});
	const targetSize = node.computeSize ? node.computeSize() : node.size || [260, 260];
	targetSize[0] = Math.max(targetSize[0], 260);
	targetSize[1] = Math.max(targetSize[1], 300);
	node.setSize?.(targetSize);
	setImagePreviewState(node, {}, "No image received");
}


function ensureCanvasImagePreviewWidget(node) {
	const img = new Image();
	node.__blenderBridgeImagePreview = {
		kind: "canvas",
		img,
		status: "No image received",
		metaText: "",
		payload: {},
	};

	const previousDrawForeground = node.onDrawForeground;
	node.onDrawForeground = function(ctx) {
		if (previousDrawForeground) {
			previousDrawForeground.apply(this, arguments);
		}
		drawCanvasImagePreview(this, ctx);
	};
	const targetSize = node.computeSize ? node.computeSize() : node.size || [260, 220];
	targetSize[0] = Math.max(targetSize[0], 260);
	targetSize[1] = Math.max(targetSize[1], 260);
	node.setSize?.(targetSize);
}


function drawCanvasImagePreview(node, ctx) {
	const preview = node?.__blenderBridgeImagePreview;
	if (!preview || preview.kind !== "canvas" || !ctx) {
		return;
	}
	const width = Math.max(1, Number(node.size?.[0]) || 260);
	const height = Math.max(1, Number(node.size?.[1]) || 260);
	const top = Math.max(88, height - 172);
	const left = 10;
	const boxW = Math.max(40, width - 20);
	const boxH = Math.max(80, height - top - 12);

	ctx.save();
	ctx.fillStyle = "rgba(0,0,0,0.18)";
	ctx.strokeStyle = "rgba(128,128,128,0.45)";
	ctx.lineWidth = 1;
	ctx.beginPath();
	ctx.roundRect?.(left, top, boxW, boxH, 4);
	if (!ctx.roundRect) {
		ctx.rect(left, top, boxW, boxH);
	}
	ctx.fill();
	ctx.stroke();

	if (preview.img?.complete && preview.img.naturalWidth > 0) {
		const fit = containRect(preview.img.naturalWidth, preview.img.naturalHeight, boxW, boxH);
		ctx.drawImage(preview.img, left + fit.x, top + fit.y, fit.w, fit.h);
	} else {
		ctx.fillStyle = "#888";
		ctx.font = "12px Arial";
		ctx.textAlign = "center";
		ctx.textBaseline = "middle";
		ctx.fillText(preview.status || "Loading image...", left + boxW / 2, top + boxH / 2);
	}

	if (preview.metaText) {
		ctx.fillStyle = "rgba(0,0,0,0.58)";
		ctx.fillRect(left + 6, top + boxH - 22, boxW - 12, 17);
		ctx.fillStyle = "#fff";
		ctx.font = "11px Arial";
		ctx.textAlign = "left";
		ctx.textBaseline = "middle";
		ctx.fillText(preview.metaText, left + 10, top + boxH - 13, boxW - 20);
	}
	ctx.restore();
}


function containRect(imageWidth, imageHeight, boxWidth, boxHeight) {
	const scale = Math.min(boxWidth / imageWidth, boxHeight / imageHeight);
	const w = imageWidth * scale;
	const h = imageHeight * scale;
	return {
		x: (boxWidth - w) / 2,
		y: (boxHeight - h) / 2,
		w,
		h,
	};
}


function patchImageReceiverWidgets(node) {
	if (!Array.isArray(node?.widgets) || node.__blenderBridgeWidgetCallbacksPatched) {
		return;
	}
	node.__blenderBridgeWidgetCallbacksPatched = true;
	for (const widget of node.widgets) {
		if (!widget || !["source_id", "channel"].includes(widget.name)) {
			continue;
		}
		const oldCallback = widget.callback;
		widget.callback = function() {
			const result = oldCallback ? oldCallback.apply(this, arguments) : undefined;
			globalThis.setTimeout?.(() => refreshImagePreviewFromLatest(node), 0);
			return result;
		};
	}
}


async function refreshImagePreviewFromLatest(node) {
	if (!isImageReceiverNode(node)) {
		return;
	}
	const source = encodeURIComponent(normalizedWidgetValue(node, "source_id", "blender") || "blender");
	const channel = encodeURIComponent(normalizedWidgetValue(node, "channel", "beauty").toLowerCase() || "beauty");
	try {
		const response = await fetch(bridgeUrl(`/blender_bridge/latest?source_id=${source}&channel=${channel}`));
		if (!response.ok) {
			setImagePreviewState(node, {}, "No image received");
			return;
		}
		const data = await response.json();
		if (data?.metadata) {
			setImagePreviewState(node, data.metadata);
		}
	} catch (_err) {
		setImagePreviewState(node, {}, "Preview unavailable");
	}
}


function clearOverlay(preview) {
	const canvas = preview?.overlay;
	if (!canvas) {
		return;
	}
	const ctx = canvas.getContext("2d");
	if (ctx) {
		ctx.clearRect(0, 0, canvas.width, canvas.height);
	}
}


async function drawPoseOverlay(preview, payload) {
	if (!preview?.overlay || payload.channel !== "beauty") {
		clearOverlay(preview);
		return;
	}
	try {
		const source = encodeURIComponent(payload.source_id || "blender");
		const response = await fetch(bridgeUrl(`/blender_bridge/latest?source_id=${source}&channel=pose`));
		if (!response.ok) {
			clearOverlay(preview);
			return;
		}
		const data = await response.json();
		const pose = data?.metadata?.pose_json;
		if (!pose || !Array.isArray(pose.people)) {
			clearOverlay(preview);
			return;
		}
		renderPoseOverlay(preview, pose);
	} catch (_err) {
		clearOverlay(preview);
	}
}


function renderPoseOverlay(preview, pose) {
	const canvas = preview.overlay;
	const img = preview.img;
	const rect = preview.container.getBoundingClientRect();
	const width = Math.max(1, Math.round(rect.width));
	const height = Math.max(1, Math.round(rect.height));
	if (canvas.width !== width || canvas.height !== height) {
		canvas.width = width;
		canvas.height = height;
	}
	const ctx = canvas.getContext("2d");
	if (!ctx) {
		return;
	}
	ctx.clearRect(0, 0, width, height);
	const poseWidth = Number(pose.canvas_width) || Number(img.naturalWidth) || width;
	const poseHeight = Number(pose.canvas_height) || Number(img.naturalHeight) || height;
	const imageWidth = Number(img.naturalWidth) || poseWidth;
	const imageHeight = Number(img.naturalHeight) || poseHeight;
	const scale = Math.min(width / imageWidth, height / imageHeight);
	const frameW = imageWidth * scale;
	const frameH = imageHeight * scale;
	const offsetX = (width - frameW) / 2;
	const offsetY = (height - frameH) / 2;
	const pointScaleX = frameW / poseWidth;
	const pointScaleY = frameH / poseHeight;

	ctx.save();
	ctx.lineWidth = 2;
	ctx.strokeStyle = "rgba(0, 0, 0, 0.85)";
	ctx.fillStyle = "rgba(255, 72, 72, 0.95)";
	for (const person of pose.people) {
		const points = Array.isArray(person?.pose_keypoints_2d) ? person.pose_keypoints_2d : [];
		for (let index = 0; index + 2 < points.length; index += 3) {
			const confidence = Number(points[index + 2]);
			if (!Number.isFinite(confidence) || confidence <= 0) {
				continue;
			}
			const x = offsetX + Number(points[index]) * pointScaleX;
			const y = offsetY + Number(points[index + 1]) * pointScaleY;
			if (!Number.isFinite(x) || !Number.isFinite(y)) {
				continue;
			}
			ctx.beginPath();
			ctx.arc(x, y, 3.5, 0, Math.PI * 2);
			ctx.stroke();
			ctx.fill();
		}
	}
	ctx.restore();
}


function receiverMatchesPayload(node, payload) {
	const sourceId = normalizedWidgetValue(node, "source_id", "blender") || "blender";
	const channel = normalizedWidgetValue(node, "channel", "beauty").toLowerCase() || "beauty";
	const payloadSource = String(payload.source_id || "blender").trim();
	const payloadChannel = String(payload.channel || "").trim().toLowerCase();
	return sourceId === payloadSource && channel === payloadChannel;
}


function applyImageEvent(payload) {
	if (!payload?.source_id || !payload?.channel) {
		return;
	}
	let updated = 0;
	for (const node of graphNodes()) {
		if (!isImageReceiverNode(node) || !receiverMatchesPayload(node, payload)) {
			continue;
		}
		ensureImagePreviewWidget(node);
		setImagePreviewState(node, payload);
		updated += 1;
	}
	const size = payload.width && payload.height ? `${payload.width}x${payload.height}` : "unknown size";
	const suffix = updated > 0 ? ` Updated ${updated} receiver preview${updated === 1 ? "" : "s"}.` : "";
	showToast("info", "Blender Bridge", `${payload.channel} image received from ${payload.source_id} (${size}).${suffix}`, 2500);
}


function pushUniqueMenuOption(options, option) {
	if (!Array.isArray(options) || !option?.content) {
		return;
	}
	if (options.some((existing) => existing?.content === option.content)) {
		return;
	}
	options.push(option);
}


app.registerExtension({
	name: "Blender.Bridge",

	async setup() {
		if (globalThis[SETUP_FLAG]) {
			return;
		}
		globalThis[SETUP_FLAG] = true;
		const apiObj = bridgeApi();
		if (!apiObj?.addEventListener) {
			console.warn("[ComfyUI-Blender-Bridge] ComfyUI api event listener is unavailable.");
			return;
		}
		apiObj.addEventListener("blender_bridge.pose", (event) => {
			void applyPoseEvent(eventPayload(event));
		});
		apiObj.addEventListener("blender_bridge.image", (event) => {
			applyImageEvent(eventPayload(event));
		});
		globalThis.setTimeout?.(() => {
			for (const node of graphNodes()) {
				if (isOpenPoseStudioNode(node)) {
					ensureTargetId(node);
				}
				if (isImageReceiverNode(node)) {
					ensureImagePreviewWidget(node);
				}
			}
		}, 500);
	},

	loadedGraphNode(node) {
		if (isOpenPoseStudioNode(node)) {
			ensureTargetId(node);
		}
		if (isImageReceiverNode(node)) {
			ensureImagePreviewWidget(node);
		}
	},

	async beforeRegisterNodeDef(nodeType, nodeData) {
		const className = nodeData?.name || nodeData?.display_name || nodeData?.title || "";
		if (isOpenPoseClassName(className) && !nodeType.prototype[OPENPOSE_PATCH_FLAG]) {
			nodeType.prototype[OPENPOSE_PATCH_FLAG] = true;

			const onNodeCreated = nodeType.prototype.onNodeCreated;
			nodeType.prototype.onNodeCreated = function() {
				const result = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
				ensureTargetId(this);
				return result;
			};

			const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
			nodeType.prototype.getExtraMenuOptions = function(_, options) {
				const result = getExtraMenuOptions ? getExtraMenuOptions.apply(this, arguments) : undefined;
				const targetId = ensureTargetId(this);
				pushUniqueMenuOption(options, {
					content: "Copy Bridge Target ID",
					callback: () => {
						const ok = copyText(targetId);
						showToast(ok ? "success" : "warn", "Blender Bridge", ok ? "Bridge target ID copied." : targetId);
					},
				});
				pushUniqueMenuOption(options, {
					content: "Set Active Blender Target",
					callback: () => {
						try {
							globalThis.localStorage?.setItem(ACTIVE_TARGET_KEY, targetId);
						} catch (_err) {
							// Ignore storage failures; the copied target id still works manually.
						}
						showToast("success", "Blender Bridge", "This OpenPose Studio node is now the active Blender target.");
					},
				});
				return result;
			};
			return;
		}

		if (isImageReceiverClassName(className) && !nodeType.prototype[IMAGE_PATCH_FLAG]) {
			nodeType.prototype[IMAGE_PATCH_FLAG] = true;
			const onNodeCreated = nodeType.prototype.onNodeCreated;
			nodeType.prototype.onNodeCreated = function() {
				const result = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
				ensureImagePreviewWidget(this);
				return result;
			};
		}
	},
});
