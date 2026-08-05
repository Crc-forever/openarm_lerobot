import { createSystem, Quaternion, Vector3, Visibility } from "@iwsdk/core";
import { getClientId } from "../client_id";
import {
	type TeleopLifecycle,
	type TeleopSettings,
	type TeleopTelemetry,
	useAppStore,
} from "../lib/store";
import { setCameraViewsConfig } from "./camera_views";
import { GlobalRefs } from "./global_refs";
import {
	ControllerButton,
	describeController,
	isControllerButtonPressed,
	resolveControllerSpace,
	type StatefulGamepadLike,
} from "./input_compat";
import { RobotModelSystem } from "./robot_system";

type DevicePose = {
	position: { x: number; y: number; z: number };
	orientation: { x: number; y: number; z: number; w: number };
};

type WireGamepad = {
	buttons: Array<{ pressed: boolean; touched: boolean; value: number }>;
	axes: number[];
	profiles: string[];
	mapping: string;
};

export class TeleopSystem extends createSystem({}) {
	private ws: WebSocket | null = null;
	private frameCount = 0;
	private lastFpsTime = 0;
	private currentFps = 0;
	private lastSendTime = 0;
	private updateInterval = 0.01;
	private tempPosition = new Vector3();
	private tempQuaternion = new Quaternion();
	private menuButtonState = false;
	public inputMode: string | null = null;
	private clientId = getClientId();
	private reconnectTimer: number | null = null;
	private reconnectAttempt = 0;
	private loggedControllerSignatures = new Set<string>();
	private disposed = false;

	init() {
		this.connectWS();

		const unsubscribe = useAppStore.subscribe((state) => {
			this.updateInterval = 1 / state.advancedSettings.updateRate;
		});
		this.cleanupFuncs.push(unsubscribe, () => this.disposeConnection());
		this.updateInterval =
			1 / useAppStore.getState().advancedSettings.updateRate;
	}

	connectWS() {
		if (this.disposed) return;

		useAppStore.getState().setConnectionStatus("connecting");
		this.setLifecycle(
			this.reconnectAttempt > 0 ? "reconnecting" : "connecting",
		);
		const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
		const wsUrl = `${protocol}//${window.location.host}/ws`;

		const ws = new WebSocket(wsUrl);
		this.ws = ws;

		ws.onopen = () => {
			if (this.disposed || this.ws !== ws) {
				ws.close();
				return;
			}
			this.reconnectAttempt = 0;
			this.clearReconnectTimer();
			this.updateStatus(true);
			this.setLifecycle("connected");
		};

		ws.onclose = () => {
			if (this.ws === ws) {
				this.ws = null;
			}
			if (this.disposed) return;
			this.updateStatus(false);
			this.setLifecycle("reconnecting");
			useAppStore.getState().setTeleopEngaged(false);
			this.scheduleReconnect();
		};

		ws.onerror = (error) => {
			console.error("WS Error", error);
			this.setLifecycle("error");
		};

		ws.onmessage = (event) => {
			try {
				const message = JSON.parse(event.data);
				if (message.type === "config") {
					if (message.data?.input_mode) {
						this.inputMode = message.data.input_mode;
					}
					// Apply default speed from server if provided
					if (typeof message.data?.speed === "number") {
						useAppStore.getState().setTeleopSettings({
							speed: message.data.speed,
						});
					}
					const cameraViews = message.data?.camera_views ?? null;
					const availableCameraKeys = cameraViews
						? Object.keys(cameraViews)
						: [];
					useAppStore.getState().setAvailableCameras(availableCameraKeys);
					setCameraViewsConfig(cameraViews);
				} else if (message.type === "robot_config") {
					this.setLifecycle("loading_robot");
					const robotSystem = this.world.getSystem(RobotModelSystem);
					if (robotSystem) {
						robotSystem.onRobotConfig(message.data);
					}
				} else if (message.type === "robot_state") {
					const robotSystem = this.world.getSystem(RobotModelSystem);
					if (robotSystem) {
						robotSystem.onRobotState(message.data);
					}
				} else if (message.type === "control_frame_reset") {
					// Keep the visual robot aligned with the newly captured
					// operator-forward frame. This affects display placement only.
					const heading = Number(message.data?.yaw_rad);
					useAppStore
						.getState()
						.setRobotResetTrigger(
							Date.now(),
							Number.isFinite(heading) ? heading : undefined,
						);
					console.info("[TeleopSystem] Operator control frame reset");
				}
			} catch (error) {
				console.warn("Failed to parse WS message", error);
			}
		};
	}

	private scheduleReconnect() {
		if (this.disposed || this.reconnectTimer !== null) {
			return;
		}

		const delayMs = Math.min(3000 * 2 ** this.reconnectAttempt, 15000);
		this.reconnectAttempt += 1;

		this.reconnectTimer = window.setTimeout(() => {
			this.reconnectTimer = null;
			if (!this.disposed) {
				this.connectWS();
			}
		}, delayMs);
	}

	private clearReconnectTimer() {
		if (this.reconnectTimer === null) {
			return;
		}
		window.clearTimeout(this.reconnectTimer);
		this.reconnectTimer = null;
	}

	private disposeConnection() {
		if (this.disposed) return;
		this.disposed = true;
		this.clearReconnectTimer();
		useAppStore.getState().setTeleopEngaged(false);

		const ws = this.ws;
		this.ws = null;
		if (ws) {
			ws.onopen = null;
			ws.onmessage = null;
			ws.onerror = null;
			ws.onclose = null;
			if (
				ws.readyState === WebSocket.OPEN ||
				ws.readyState === WebSocket.CONNECTING
			) {
				ws.close();
			}
		}
	}

	private setLifecycle(lifecycle: TeleopLifecycle) {
		useAppStore.getState().setTeleopLifecycle(lifecycle);
	}

	updateStatus(connected: boolean) {
		useAppStore
			.getState()
			.setConnectionStatus(connected ? "connected" : "disconnected");
	}

	// biome-ignore lint/suspicious/noExplicitAny: legacy
	poseFromObject(object: any): DevicePose | null {
		if (!object?.getWorldPosition || !object?.getWorldQuaternion) {
			return null;
		}
		object.getWorldPosition(this.tempPosition);
		object.getWorldQuaternion(this.tempQuaternion);
		return {
			position: {
				x: this.tempPosition.x,
				y: this.tempPosition.y,
				z: this.tempPosition.z,
			},
			orientation: {
				x: this.tempQuaternion.x,
				y: this.tempQuaternion.y,
				z: this.tempQuaternion.z,
				w: this.tempQuaternion.w,
			},
		};
	}

	buildControllerDevice(
		handedness: "left" | "right",
		// biome-ignore lint/suspicious/noExplicitAny: XR input spaces are SDK-owned
		gripSpace: any,
		// biome-ignore lint/suspicious/noExplicitAny: fallback for incomplete runtimes
		raySpace: any,
		gamepad: StatefulGamepadLike | undefined,
	) {
		if (!gamepad) return null;

		// gripSpace represents the physical controller grip, including full
		// wrist orientation. target-ray space is only a compatibility fallback.
		const pose = this.poseFromObject(gripSpace ?? raySpace);
		if (!pose) {
			return null;
		}

		const device: {
			role: string;
			handedness: string;
			gripPose: DevicePose;
			gamepad?: WireGamepad;
		} = {
			role: "controller",
			handedness,
			gripPose: pose,
		};

		const rawGamepad = gamepad?.gamepad;
		if (rawGamepad) {
			const metadata = describeController(gamepad);
			device.gamepad = {
				// biome-ignore lint/suspicious/noExplicitAny: legacy
				buttons: Array.from(rawGamepad.buttons ?? []).map((button: any) => ({
					pressed: button.pressed,
					touched: button.touched,
					value: button.value,
				})),
				axes: Array.from(rawGamepad.axes ?? []),
				profiles: metadata?.profiles ?? [],
				mapping: metadata?.mapping ?? "",
			};
		}

		return device;
	}

	private logControllerCompatibility(
		handedness: "left" | "right",
		controller: StatefulGamepadLike | undefined,
	) {
		const metadata = describeController(controller);
		if (!metadata) return;

		const signature = `${handedness}:${metadata.profiles.join(",")}:${metadata.mapping}:${metadata.buttonCount}:${metadata.axesCount}`;
		if (this.loggedControllerSignatures.has(signature)) return;
		this.loggedControllerSignatures.add(signature);

		console.info(
			`[XRInputCompat] ${handedness} controller`,
			JSON.stringify(metadata),
		);
	}

	update(_delta: number, time: number) {
		if (this.lastFpsTime === 0) {
			this.lastFpsTime = time;
		}

		this.frameCount += 1;
		if (time - this.lastFpsTime >= 1.0) {
			this.currentFps = Math.round(this.frameCount / (time - this.lastFpsTime));
			this.frameCount = 0;
			this.lastFpsTime = time;
		}

		if (time - this.lastSendTime <= this.updateInterval) {
			return;
		}
		this.lastSendTime = time;
		// biome-ignore lint/suspicious/noExplicitAny: legacy
		const input = (this as any).input ?? this.world.input;

		const appState = useAppStore.getState();
		appState.setTeleopEngaged(this.isTeleopEngaged(input));
		const { speed, turnSpeed, precisionMode } = appState.teleopSettings;
		const teleopSettings: TeleopSettings = {
			speed,
			turnSpeed,
			precisionMode,
		};
		const state = this.gatherInputState(input, teleopSettings);

		const latency = state ? state.fetch_latency_ms : 0;
		const head = state?.devices.find((device) => device.role === "head");
		this.updateLocalStats(
			head?.pose ?? null,
			this.currentFps,
			latency,
			appState.setTeleopTelemetry,
		);

		if (!state || state.devices.length === 0) {
			return;
		}

		if (this.ws && this.ws.readyState === WebSocket.OPEN) {
			this.ws.send(
				JSON.stringify({
					type: "xr_state",
					client_id: this.clientId,
					data: state,
				}),
			);
		}
	}

	updateLocalStats(
		_pose: DevicePose | null,
		fps: number,
		latency: number,
		setTeleopTelemetry: (telemetry: TeleopTelemetry) => void,
	) {
		const latencyMsValue = Number.isFinite(latency) ? latency : 0;
		setTeleopTelemetry({ fps, latencyMs: latencyMsValue });
	}

	// biome-ignore lint/suspicious/noExplicitAny: legacy
	private isTeleopEngaged(input: any): boolean {
		const leftSqueezed = isControllerButtonPressed(
			input?.gamepads?.left,
			ControllerButton.squeeze,
			1,
		);
		const rightSqueezed = isControllerButtonPressed(
			input?.gamepads?.right,
			ControllerButton.squeeze,
			1,
		);

		return leftSqueezed && rightSqueezed;
	}

	// biome-ignore lint/suspicious/noExplicitAny: legacy
	gatherInputState(input: any, teleopSettings: TeleopSettings) {
		const leftController = input?.gamepads?.left as
			| StatefulGamepadLike
			| undefined;
		const rightController = input?.gamepads?.right as
			| StatefulGamepadLike
			| undefined;
		this.logControllerCompatibility("left", leftController);
		this.logControllerCompatibility("right", rightController);

		// Only use a true semantic menu component. On PICO 4/4 Ultra the last
		// app-visible button is Y, so treating the last array entry as Menu
		// conflicts with Y-drag.
		const menuPressed = isControllerButtonPressed(
			leftController,
			ControllerButton.menu,
			Number.MAX_SAFE_INTEGER,
		);
		if (menuPressed) {
			if (!this.menuButtonState) {
				this.menuButtonState = true;
				const teleopPanelRoot = GlobalRefs.teleopPanelRoot;
				if (teleopPanelRoot?.entity?.hasComponent(Visibility)) {
					const currentVisibility = teleopPanelRoot.entity.getValue(
						Visibility,
						"isVisible",
					);
					teleopPanelRoot.entity.setValue(
						Visibility,
						"isVisible",
						!currentVisibility,
					);
				}
			}
		} else {
			this.menuButtonState = false;
		}

		// biome-ignore lint/suspicious/noExplicitAny: SDK player type is runtime-owned
		const player = (this as any).player ?? this.world.player;
		const leftGrip = resolveControllerSpace(
			player,
			input,
			"left",
			"grip",
		);
		const leftRay = resolveControllerSpace(player, input, "left", "ray");
		const rightGrip = resolveControllerSpace(
			player,
			input,
			"right",
			"grip",
		);
		const rightRay = resolveControllerSpace(player, input, "right", "ray");

		const fetchStart = performance.now();
		const timestamp_unix_ms = Date.now();
		const devices: Array<{
			role: string;
			handedness: string;
			pose?: DevicePose;
			gripPose?: DevicePose;
			gamepad?: WireGamepad;
		}> = [];

		const headPose = this.poseFromObject(player?.head);
		if (headPose) {
			devices.push({
				role: "head",
				handedness: "none",
				pose: headPose,
			});
		}

		const leftDevice = this.buildControllerDevice(
			"left",
			leftGrip,
			leftRay,
			leftController,
		);
		if (leftDevice) {
			devices.push(leftDevice);
		}

		const rightDevice = this.buildControllerDevice(
			"right",
			rightGrip,
			rightRay,
			rightController,
		);
		if (rightDevice) {
			devices.push(rightDevice);
		}

		if (devices.length === 0) {
			return null;
		}

		const fetch_latency_ms = performance.now() - fetchStart;

		return {
			timestamp_unix_ms,
			devices,
			fps: this.currentFps,
			fetch_latency_ms,
			teleop_settings: teleopSettings,
		};
	}
}
