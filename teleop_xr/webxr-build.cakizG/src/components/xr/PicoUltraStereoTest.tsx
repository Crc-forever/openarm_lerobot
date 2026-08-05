"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
	CanvasTexture,
	ClampToEdgeWrapping,
	Color,
	LinearFilter,
	Mesh,
	PerspectiveCamera,
	PlaneGeometry,
	Scene,
	ShaderMaterial,
	WebGLRenderer,
} from "three";

const HEADER_BYTES = 24;
const FLAG_CODEC_CONFIG = 1;
const FLAG_KEY_FRAME = 2;

type RenderResources = {
	renderer: WebGLRenderer;
	geometry: PlaneGeometry;
	materials: [ShaderMaterial, ShaderMaterial];
	texture: CanvasTexture;
	session: XRSession;
};

const vertexShader = `
	varying vec2 videoUv;
	void main() {
		videoUv = uv;
		gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
	}
`;

const fragmentShader = `
	uniform sampler2D stereoMap;
	uniform float eyeOffset;
	varying vec2 videoUv;
	void main() {
		vec2 eyeUv = vec2(eyeOffset + videoUv.x * 0.5, 1.0 - videoUv.y);
		gl_FragColor = vec4(texture2D(stereoMap, eyeUv).rgb, 1.0);
	}
`;

function eyeMaterial(texture: CanvasTexture, eyeOffset: 0 | 0.5) {
	return new ShaderMaterial({
		uniforms: { stereoMap: { value: texture }, eyeOffset: { value: eyeOffset } },
		vertexShader,
		fragmentShader,
		depthTest: false,
		depthWrite: false,
	});
}

function joinBytes(first: Uint8Array, second: Uint8Array): Uint8Array {
	const result = new Uint8Array(first.byteLength + second.byteLength);
	result.set(first, 0);
	result.set(second, first.byteLength);
	return result;
}

export function PicoUltraStereoTest() {
	const canvasRef = useRef<HTMLCanvasElement>(null);
	const canvasHostRef = useRef<HTMLDivElement>(null);
	const decoderRef = useRef<VideoDecoder | null>(null);
	const socketRef = useRef<WebSocket | null>(null);
	const resourcesRef = useRef<RenderResources | null>(null);
	const [senderConnected, setSenderConnected] = useState(false);
	const [videoReady, setVideoReady] = useState(false);
	const [dimensions, setDimensions] = useState("等待视频…");
	const [inXR, setInXR] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		if (!("VideoDecoder" in window)) {
			setError("当前 PICO Browser 不支持 WebCodecs VideoDecoder");
			return;
		}
		const canvas = canvasRef.current;
		if (!canvas) return;
		const context = canvas.getContext("2d", { alpha: false });
		if (!context) {
			setError("无法创建视频画布");
			return;
		}

		let disposed = false;
		let codecConfig = new Uint8Array();
		let waitingForKeyFrame = true;
		const decoder = new VideoDecoder({
			output: (frame) => {
				if (disposed) {
					frame.close();
					return;
				}
				if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
					canvas.width = frame.displayWidth;
					canvas.height = frame.displayHeight;
					setDimensions(`${frame.displayWidth}×${frame.displayHeight}`);
				}
				context.drawImage(frame, 0, 0, canvas.width, canvas.height);
				frame.close();
				setVideoReady(true);
			},
			error: (reason) => {
				waitingForKeyFrame = true;
				setError(`H.264 硬件解码失败: ${reason.message}`);
			},
		});
		decoderRef.current = decoder;

		const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
		const socket = new WebSocket(`${protocol}//${window.location.host}/ws/pico-ultra-stereo`);
		socket.binaryType = "arraybuffer";
		socketRef.current = socket;
		socket.onopen = () => setError(null);
		socket.onclose = () => {
			setSenderConnected(false);
			if (!disposed) setError("PICO Ultra 视频中继连接已断开");
		};
		socket.onerror = () => setError("无法连接 PICO Ultra 视频中继");
		socket.onmessage = (event) => {
			if (typeof event.data === "string") {
				const message = JSON.parse(event.data) as { type: string; codec?: string; sender_connected?: boolean };
				if (message.type === "stream-config" && decoder.state === "unconfigured") {
					decoder.configure({
                        codec: message.codec ?? "avc1.64002A",
						hardwareAcceleration: "prefer-hardware",
						optimizeForLatency: true,
						avc: { format: "annexb" },
					} as VideoDecoderConfig);
				}
				if (message.type === "status") setSenderConnected(Boolean(message.sender_connected));
				return;
			}

			const packet = new Uint8Array(event.data as ArrayBuffer);
			if (packet.byteLength < HEADER_BYTES || decoder.state !== "configured") return;
			const view = new DataView(packet.buffer, packet.byteOffset, packet.byteLength);
			if (String.fromCharCode(packet[0], packet[1], packet[2], packet[3]) !== "PXSV") return;
			const flags = view.getUint8(5);
			const timestamp = Number(view.getBigUint64(12, false));
			const payloadLength = view.getUint32(20, false);
			if (HEADER_BYTES + payloadLength > packet.byteLength) return;
			const payload = packet.slice(HEADER_BYTES, HEADER_BYTES + payloadLength);
			setSenderConnected(true);
			if ((flags & FLAG_CODEC_CONFIG) !== 0) {
				codecConfig = payload;
				return;
			}
			const keyFrame = (flags & FLAG_KEY_FRAME) !== 0;
			if (waitingForKeyFrame && !keyFrame) return;
			if (decoder.decodeQueueSize > 3) {
				decoder.reset();
				decoder.configure({
                    codec: "avc1.64002A",
					hardwareAcceleration: "prefer-hardware",
					optimizeForLatency: true,
					avc: { format: "annexb" },
				} as VideoDecoderConfig);
				waitingForKeyFrame = true;
				if (!keyFrame) return;
			}
			const data = keyFrame && codecConfig.byteLength > 0 ? joinBytes(codecConfig, payload) : payload;
			decoder.decode(new EncodedVideoChunk({
				type: keyFrame ? "key" : "delta",
				timestamp,
				data,
			}));
			waitingForKeyFrame = false;
		};

		return () => {
			disposed = true;
			socket.close();
			if (decoder.state !== "closed") decoder.close();
			decoderRef.current = null;
			socketRef.current = null;
		};
	}, []);

	const stopXR = useCallback(async () => {
		const resources = resourcesRef.current;
		resourcesRef.current = null;
		if (!resources) return;
		resources.renderer.setAnimationLoop(null);
		try { await resources.session.end(); } catch { /* already ended */ }
		resources.texture.dispose();
		resources.geometry.dispose();
		resources.materials.forEach((material) => material.dispose());
		resources.renderer.dispose();
		resources.renderer.domElement.remove();
		setInXR(false);
	}, []);

	useEffect(() => () => void stopXR(), [stopXR]);

	const enterXR = useCallback(async () => {
		const videoCanvas = canvasRef.current;
		const host = canvasHostRef.current;
		if (!videoReady || !videoCanvas || !host) {
			setError("请等待 Ultra 双目视频准备完成");
			return;
		}
		if (!navigator.xr) {
			setError("当前浏览器不支持 WebXR");
			return;
		}
		try {
			const scene = new Scene();
			scene.background = new Color(0x000000);
			const camera = new PerspectiveCamera(96, 1, 0.05, 10);
			const renderer = new WebGLRenderer({ antialias: false, alpha: false });
			renderer.xr.enabled = true;
			renderer.xr.setReferenceSpaceType("local-floor");
			renderer.setPixelRatio(1);
			renderer.setSize(window.innerWidth, window.innerHeight);
			host.replaceChildren(renderer.domElement);

			const texture = new CanvasTexture(videoCanvas);
			texture.generateMipmaps = false;
			texture.minFilter = LinearFilter;
			texture.magFilter = LinearFilter;
			texture.wrapS = ClampToEdgeWrapping;
			texture.wrapT = ClampToEdgeWrapping;
			const distance = 1;
			const geometry = new PlaneGeometry(2.22, 2.22);
			const materials: [ShaderMaterial, ShaderMaterial] = [eyeMaterial(texture, 0), eyeMaterial(texture, 0.5)];
			materials.forEach((material, index) => {
				const plane = new Mesh(geometry, material);
				plane.layers.set(index + 1);
				plane.frustumCulled = false;
				plane.onBeforeRender = (_renderer, _scene, renderCamera) => {
					renderCamera.getWorldPosition(plane.position);
					renderCamera.getWorldQuaternion(plane.quaternion);
					plane.translateZ(-distance);
					plane.updateMatrixWorld(true);
				};
				scene.add(plane);
			});

			const session = await navigator.xr.requestSession("immersive-vr", { optionalFeatures: ["local-floor"] });
			await renderer.xr.setSession(session);
			session.addEventListener("end", () => void stopXR(), { once: true });
			resourcesRef.current = { renderer, geometry, materials, texture, session };
			setInXR(true);
			renderer.setAnimationLoop(() => {
				texture.needsUpdate = true;
				const eyes = renderer.xr.getCamera().cameras;
				if (eyes.length >= 2) {
					eyes[0].layers.enable(1); eyes[0].layers.disable(2);
					eyes[1].layers.enable(2); eyes[1].layers.disable(1);
				}
				renderer.render(scene, camera);
			});
		} catch (reason) {
			setError(reason instanceof Error ? reason.message : String(reason));
			await stopXR();
		}
	}, [stopXR, videoReady]);

	return (
		<main className="min-h-screen bg-zinc-950 px-5 py-6 text-zinc-100">
			<div className="mx-auto max-w-5xl space-y-5">
				<header>
					<h1 className="text-3xl font-semibold">PICO 4 Ultra → PICO 4 双目视频</h1>
					<p className="mt-2 text-zinc-400">Ultra 的左右彩色相机保持物理双目视差；PICO 4 将 SBS 两半直接提交给左右眼。</p>
				</header>
				<div className="rounded-xl border border-zinc-700 bg-zinc-900 p-4">
					<div className="mb-3 flex items-center justify-between gap-3">
						<div>
							<div className="font-medium">{senderConnected ? "Ultra 已连接" : "等待 Ultra 发送器…"}</div>
							<div className="text-sm text-zinc-400">{dimensions} · H.264 硬件低延迟解码</div>
						</div>
						<button type="button" onClick={inXR ? () => void stopXR() : () => void enterXR()} disabled={!videoReady}
							className="rounded-lg bg-emerald-500 px-5 py-3 font-semibold text-black disabled:bg-zinc-700 disabled:text-zinc-400">
							{inXR ? "退出 VR" : "进入双目 VR"}
						</button>
					</div>
					<canvas ref={canvasRef} width={2} height={1} className="aspect-[2/1] w-full rounded-lg bg-black object-contain" />
					<div className="mt-2 flex justify-around text-xs text-zinc-500"><span>Ultra 左相机</span><span>Ultra 右相机</span></div>
				</div>
				{error ? <div className="rounded-lg border border-red-500/50 bg-red-950 p-4 text-red-200">{error}</div> : null}
			</div>
			<div ref={canvasHostRef} className="fixed inset-0 -z-10" />
		</main>
	);
}
