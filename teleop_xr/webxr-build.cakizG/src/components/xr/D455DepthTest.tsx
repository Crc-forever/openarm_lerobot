"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
	ClampToEdgeWrapping,
	Color,
	LinearFilter,
	Mesh,
	PerspectiveCamera,
	PlaneGeometry,
	Scene,
	ShaderMaterial,
	VideoTexture,
	WebGLRenderer,
} from "three";
import { VideoClient, type VideoStats } from "@/xr/video";

type D455StereoConfig = {
	stream_id: string;
	layout: "stereo-left-right";
	eye_width: number;
	eye_height: number;
	composite_width: number;
	composite_height: number;
	fps: number;
	projection_mode: "rgb-textured-physical-infrared-stereo";
	stereo_baseline_m: number;
	codec: string;
	target_bitrate_kbps: number;
	horizontal_fov_deg: number;
	vertical_fov_deg: number;
	serial: string;
};

type RenderResources = {
	renderer: WebGLRenderer;
	geometry: PlaneGeometry;
	materials: [ShaderMaterial, ShaderMaterial];
	texture: VideoTexture;
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
		vec2 eyeUv = vec2(eyeOffset + videoUv.x * 0.5, videoUv.y);
		gl_FragColor = vec4(texture2D(stereoMap, eyeUv).rgb, 1.0);
	}
`;

function streamSummary(stats: VideoStats | null): string {
	const stream = stats?.streams[0];
	if (!stream) return "等待 WebRTC 双目视频…";
	return `${stream.width}×${stream.height} · ${stream.fps.toFixed(0)} FPS · ${stream.bitrateKbps} kbps · 解码 ${stream.decodeMs} ms · 缓冲 ${stream.jitterBufferMs} ms`;
}

function makeEyeMaterial(texture: VideoTexture, eyeOffset: 0 | 0.5) {
	return new ShaderMaterial({
		uniforms: {
			stereoMap: { value: texture },
			eyeOffset: { value: eyeOffset },
		},
		vertexShader,
		fragmentShader,
		depthTest: false,
		depthWrite: false,
	});
}

export function D455DepthTest() {
	const videoRef = useRef<HTMLVideoElement>(null);
	const canvasHostRef = useRef<HTMLDivElement>(null);
	const resourcesRef = useRef<RenderResources | null>(null);
	const [config, setConfig] = useState<D455StereoConfig | null>(null);
	const [stats, setStats] = useState<VideoStats | null>(null);
	const [videoReady, setVideoReady] = useState(false);
	const [inXR, setInXR] = useState(false);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		fetch("/api/d455-depth-test/config", { cache: "no-store" })
			.then(async (response) => {
				const body = await response.json();
				if (!response.ok) throw new Error(body.error || "D455 配置读取失败");
				return body as D455StereoConfig;
			})
			.then((value) => {
				if (!cancelled) setConfig(value);
			})
			.catch((reason) => {
				if (!cancelled) setError(String(reason));
			});

		const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
		const client = new VideoClient(
			`${protocol}//${window.location.host}/ws`,
			(value) => setStats(value),
			(track, trackId) => {
				if (trackId !== "d455_stereo") return;
				const video = videoRef.current;
				if (!video) return;
				video.srcObject = new MediaStream([track]);
				video.playsInline = true;
				video.muted = true;
				video.onloadeddata = () => setVideoReady(true);
				video.play().catch((reason) => setError(`视频播放失败: ${reason}`));
			},
		);

		return () => {
			cancelled = true;
			client.dispose();
		};
	}, []);

	const stopXR = useCallback(async () => {
		const resources = resourcesRef.current;
		resourcesRef.current = null;
		if (!resources) return;
		resources.renderer.setAnimationLoop(null);
		try {
			await resources.session.end();
		} catch {
			// The Pico system UI may already have ended the session.
		}
		resources.texture.dispose();
		resources.geometry.dispose();
		for (const material of resources.materials) material.dispose();
		resources.renderer.dispose();
		resources.renderer.domElement.remove();
		setInXR(false);
	}, []);

	useEffect(() => () => void stopXR(), [stopXR]);

	const enterXR = useCallback(async () => {
		if (!config || !videoReady || !videoRef.current || !canvasHostRef.current) {
			setError("请等待 D455 双目视频准备完成");
			return;
		}
		if (!navigator.xr) {
			setError("当前浏览器不支持 WebXR");
			return;
		}

		try {
			setError(null);
			const scene = new Scene();
			scene.background = new Color(0x000000);
			const camera = new PerspectiveCamera(70, 1, 0.05, 10);
			const renderer = new WebGLRenderer({ antialias: false, alpha: false });
			renderer.xr.enabled = true;
			renderer.xr.setReferenceSpaceType("local-floor");
			renderer.setPixelRatio(1);
			renderer.setSize(window.innerWidth, window.innerHeight);
			canvasHostRef.current.replaceChildren(renderer.domElement);

			const texture = new VideoTexture(videoRef.current);
			texture.generateMipmaps = false;
			texture.minFilter = LinearFilter;
			texture.magFilter = LinearFilter;
			texture.wrapS = ClampToEdgeWrapping;
			texture.wrapT = ClampToEdgeWrapping;

			const distance = 1;
			const planeWidth =
				2 * distance * Math.tan((config.horizontal_fov_deg * Math.PI) / 360);
			const planeHeight =
				2 * distance * Math.tan((config.vertical_fov_deg * Math.PI) / 360);
			const geometry = new PlaneGeometry(planeWidth, planeHeight);
			const materials: [ShaderMaterial, ShaderMaterial] = [
				makeEyeMaterial(texture, 0),
				makeEyeMaterial(texture, 0.5),
			];
			materials.forEach((material, index) => {
				const plane = new Mesh(geometry, material);
				plane.layers.set(index + 1);
				plane.frustumCulled = false;
				plane.renderOrder = 1000;
				plane.onBeforeRender = (_renderer, _scene, renderCamera) => {
					// Use the current predicted eye pose supplied by WebXR during this
					// exact eye pass, avoiding a one-frame head-pose lag.
					renderCamera.getWorldPosition(plane.position);
					renderCamera.getWorldQuaternion(plane.quaternion);
					plane.translateZ(-distance);
					plane.updateMatrixWorld(true);
				};
				scene.add(plane);
			});

			const session = await navigator.xr.requestSession("immersive-vr", {
				optionalFeatures: ["local-floor"],
			});
			await renderer.xr.setSession(session);
			session.addEventListener("end", () => void stopXR(), { once: true });
			resourcesRef.current = { renderer, geometry, materials, texture, session };
			setInXR(true);

			renderer.setAnimationLoop(() => {
				const xrCamera = renderer.xr.getCamera();
				const eyeCameras = xrCamera.cameras;
				if (eyeCameras.length >= 2) {
					for (let index = 0; index < 2; index += 1) {
						const eyeCamera = eyeCameras[index];
						const ownLayer = index + 1;
						const otherLayer = index === 0 ? 2 : 1;
						eyeCamera.layers.enable(ownLayer);
						eyeCamera.layers.disable(otherLayer);
					}
				}
				renderer.render(scene, camera);
			});
		} catch (reason) {
			setError(reason instanceof Error ? reason.message : String(reason));
			await stopXR();
		}
	}, [config, stopXR, videoReady]);

	return (
		<main className="min-h-screen bg-zinc-950 px-5 py-6 text-zinc-100">
			<div className="mx-auto max-w-5xl space-y-5">
				<header>
					<h1 className="text-3xl font-semibold">D455 → PICO 双目 RGB 测试</h1>
					<p className="mt-2 text-zinc-400">
						D455 的 RGB 纹理已投影到真实左右红外相机视角；Pico 只负责把 SBS 两半分别提交给左右眼。
					</p>
				</header>

				<div className="rounded-xl border border-zinc-700 bg-zinc-900 p-4">
					<div className="mb-3 flex flex-wrap items-center justify-between gap-3">
						<div>
							<div className="font-medium">
								{videoReady ? "双目 RGB 已连接" : "正在连接 D455…"}
							</div>
							<div className="text-sm text-zinc-400">{streamSummary(stats)}</div>
						</div>
						<button
							type="button"
							onClick={inXR ? () => void stopXR() : () => void enterXR()}
							disabled={!videoReady || !config}
							className="rounded-lg bg-emerald-500 px-5 py-3 font-semibold text-black disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-400"
						>
							{inXR ? "退出 VR" : "进入双目 RGB"}
						</button>
					</div>
					<video
						ref={videoRef}
						className="aspect-[32/9] w-full rounded-lg bg-black object-contain"
						autoPlay
						muted
						playsInline
					/>
					<div className="mt-2 flex justify-around text-xs text-zinc-500">
						<span>左眼 RGB</span>
						<span>右眼 RGB</span>
					</div>
				</div>

				{config ? (
					<div className="grid gap-3 text-sm text-zinc-300 sm:grid-cols-3">
						<div className="rounded-lg bg-zinc-900 p-3">设备：{config.serial}</div>
						<div className="rounded-lg bg-zinc-900 p-3">
							每眼：{config.eye_width}×{config.eye_height}@{config.fps}
						</div>
						<div className="rounded-lg bg-zinc-900 p-3">
							{config.codec}：{config.target_bitrate_kbps} kbps · D455 基线 {Math.round(config.stereo_baseline_m * 1000)} mm
						</div>
					</div>
				) : null}

				{error ? (
					<div className="rounded-lg border border-red-500/50 bg-red-950 p-4 text-red-200">
						{error}
					</div>
				) : null}
			</div>
			<div ref={canvasHostRef} className="fixed inset-0 -z-10" />
		</main>
	);
}
