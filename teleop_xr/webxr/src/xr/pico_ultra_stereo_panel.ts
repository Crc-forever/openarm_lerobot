import { createSystem, type World } from "@iwsdk/core";
import {
	ClampToEdgeWrapping,
	DoubleSide,
	LinearFilter,
	Mesh,
	PlaneGeometry,
	ShaderMaterial,
	VideoTexture,
} from "three";
import { DraggablePanel } from "./panels";

const VIDEO_WIDTH_METERS = 1.4;
const VIDEO_HEIGHT_METERS = VIDEO_WIDTH_METERS * (960 / 1280);

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
	uniform float convergenceShift;
	varying vec2 videoUv;
	void main() {
		float correctedX = clamp(videoUv.x + convergenceShift, 0.0, 1.0);
		vec2 eyeUv = vec2(eyeOffset + correctedX * 0.5, videoUv.y);
		gl_FragColor = vec4(texture2D(stereoMap, eyeUv).rgb, 1.0);
	}
`;

type SignalMessage = {
	type: string;
	sdp?: string;
	candidate?: string;
	mid?: string;
	connected?: boolean;
};

function createStereoMaterial(
	texture: VideoTexture,
	eyeOffset: 0 | 0.5,
	convergenceShift: number,
) {
	return new ShaderMaterial({
		uniforms: {
			stereoMap: { value: texture },
			eyeOffset: { value: eyeOffset },
			convergenceShift: { value: convergenceShift },
		},
		vertexShader,
		fragmentShader,
		// This mesh sits in front of the UIKit window background. It must write
		// depth or UIKit's later transparent pass paints the black panel over it.
		depthTest: true,
		depthWrite: true,
		side: DoubleSide,
	});
}

/**
 * Keep the two physical camera halves on explicit WebXR eye layers.
 * WebXR orders the sub-cameras left then right; this is the same mapping used
 * by the standalone stereo viewer that was verified on the PICO 4.
 */
export class PicoUltraStereoEyeSystem extends createSystem({}) {
	update() {
		const eyes = this.world.renderer.xr.getCamera().cameras;
		if (eyes.length < 2) return;

		eyes[0].layers.enable(1);
		eyes[0].layers.disable(2);
		eyes[1].layers.enable(2);
		eyes[1].layers.disable(1);
	}
}

/**
 * Large movable stereo window fed by the native PICO Ultra WebRTC sender.
 * Two fixed eye-layer meshes use the exact left/right mapping from the
 * standalone viewer. No crop uniform is shared or changed between eye passes.
 */
export class PicoUltraStereoPanel extends DraggablePanel {
	private video = document.createElement("video");
	private texture: VideoTexture;
	private geometry = new PlaneGeometry(
		VIDEO_WIDTH_METERS,
		VIDEO_HEIGHT_METERS,
	);
	private materials: [ShaderMaterial, ShaderMaterial];
	private meshes: [Mesh, Mesh];
	private peer: RTCPeerConnection | null = null;
	private socket: WebSocket | null = null;
	private disposed = false;

	constructor(world: World) {
		super(world, "./ui/pico-ultra-stereo.json", {
			maxWidth: 1.55,
			maxHeight: 1.2,
			yButtonDrag: true,
		});

		this.video.autoplay = true;
		this.video.muted = true;
		this.video.playsInline = true;
		this.video.style.display = "none";
		document.body.appendChild(this.video);

		this.texture = new VideoTexture(this.video);
		this.texture.generateMipmaps = false;
		this.texture.minFilter = LinearFilter;
		this.texture.magFilter = LinearFilter;
		this.texture.wrapS = ClampToEdgeWrapping;
		this.texture.wrapT = ClampToEdgeWrapping;

		this.materials = [
			// About half of PICO's eye baseline divided by the 1.4 m video width.
			// Opposite shifts remove the virtual plane's extra disparity while
			// retaining the disparity already captured by Ultra's two cameras.
			createStereoMaterial(this.texture, 0, 0.022),
			createStereoMaterial(this.texture, 0.5, -0.022),
		];
		const leftMesh = new Mesh(this.geometry, this.materials[0]);
		const rightMesh = new Mesh(this.geometry, this.materials[1]);
		this.meshes = [leftMesh, rightMesh];
		this.meshes.forEach((mesh, index) => {
			mesh.name = `pico-ultra-stereo-video-${index === 0 ? "left" : "right"}`;
			mesh.position.set(0, -0.035, 0.025);
			mesh.layers.set(index + 1);
			mesh.renderOrder = 100;
			mesh.frustumCulled = false;
			this.panelEntity.object3D?.add(mesh);
		});

		this.connect();
	}

	private connect() {
		const peer = new RTCPeerConnection({ bundlePolicy: "max-bundle" });
		const pendingCandidates: RTCIceCandidateInit[] = [];
		const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
		const socket = new WebSocket(
			`${protocol}//${window.location.host}/ws/pico-ultra-rtc-signal`,
		);
		this.peer = peer;
		this.socket = socket;

		peer.ontrack = (event) => {
			if (this.disposed) return;
			this.video.srcObject =
				event.streams[0] ?? new MediaStream([event.track]);
			void this.video.play().catch((error) => {
				console.error("[PicoUltra3D] Video play failed:", error);
			});
		};

		peer.onconnectionstatechange = () => {
			console.info(`[PicoUltra3D] WebRTC ${peer.connectionState}`);
		};

		peer.onicecandidate = (event) => {
			if (!event.candidate || socket.readyState !== WebSocket.OPEN) return;
			socket.send(
				JSON.stringify({
					type: "candidate",
					candidate: event.candidate.candidate,
					mid: event.candidate.sdpMid,
				}),
			);
		};

		socket.onerror = () => {
			console.error("[PicoUltra3D] WebRTC signaling connection failed");
		};
		socket.onmessage = (event) => {
			void this.handleSignal(event.data, pendingCandidates).catch((error) => {
				console.error("[PicoUltra3D] Signaling message failed:", error);
			});
		};
	}

	private async handleSignal(
		data: string,
		pendingCandidates: RTCIceCandidateInit[],
	) {
		if (this.disposed || !this.peer || !this.socket) return;
		const message = JSON.parse(data) as SignalMessage;
		if (message.type === "offer" && message.sdp) {
			await this.peer.setRemoteDescription({ type: "offer", sdp: message.sdp });
			await this.peer.setLocalDescription(await this.peer.createAnswer());
			this.socket.send(
				JSON.stringify({ type: "answer", sdp: this.peer.localDescription?.sdp }),
			);
			for (const candidate of pendingCandidates.splice(0)) {
				await this.peer.addIceCandidate(candidate);
			}
			return;
		}

		if (message.type === "candidate" && message.candidate) {
			const candidate: RTCIceCandidateInit = {
				candidate: message.candidate.startsWith("candidate:")
					? message.candidate
					: `candidate:${message.candidate}`,
				sdpMid: message.mid ?? "video",
			};
			if (this.peer.remoteDescription) {
				await this.peer.addIceCandidate(candidate);
			} else {
				pendingCandidates.push(candidate);
			}
		}
	}

	dispose() {
		if (this.disposed) return;
		this.disposed = true;
		this.socket?.close();
		this.peer?.close();
		this.socket = null;
		this.peer = null;
		this.video.pause();
		this.video.srcObject = null;
		this.video.remove();
		for (const mesh of this.meshes) mesh.removeFromParent();
		this.geometry.dispose();
		for (const material of this.materials) material.dispose();
		this.texture.dispose();
		super.dispose();
	}
}
