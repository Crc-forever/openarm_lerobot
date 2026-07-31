import {
	createComponent,
	createSystem,
	DistanceGrabbable,
	eq,
	Hovered,
	Interactable,
	MovementMode,
	PanelDocument,
	PanelUI,
	Types,
	type UIKitDocument,
	Visibility,
	type World,
} from "@iwsdk/core";

export type Entity = ReturnType<World["createTransformEntity"]>;

import {
	BoxGeometry,
	DoubleSide,
	type Material,
	Mesh,
	MeshBasicMaterial,
	type Object3D,
	PlaneGeometry,
	Quaternion,
	Raycaster,
	Vector3,
	VideoTexture,
} from "three";
import { useAppStore } from "../lib/store";
import { GlobalRefs } from "./global_refs";
import {
	ControllerButton,
	isControllerButtonPressed,
	resolveControllerSpace,
} from "./input_compat";
import { faceViewerUpright } from "./panel_facing_logic";
import { shouldBeginYDrag, shouldEndYDrag } from "./panel_y_drag_logic";

type MaterialWithMap = Material & {
	map?: { dispose: () => void };
};

export const CameraPanelInfo = createComponent("CameraPanelInfo", {
	label: { type: Types.String, default: "" },
});

export const PanelHandle = createComponent("PanelHandle", {
	originalPosZ: { type: Types.Float32, default: 0 },
	originalScaleX: { type: Types.Float32, default: 1 },
	originalScaleY: { type: Types.Float32, default: 1 },
	originalScaleZ: { type: Types.Float32, default: 1 },
	originalColorR: { type: Types.Float32, default: 1 },
	originalColorG: { type: Types.Float32, default: 1 },
	originalColorB: { type: Types.Float32, default: 1 },
	visualState: { type: Types.Boolean, default: false },
	cooldown: { type: Types.Float32, default: 0 },
	panelEntityId: { type: Types.Float32, default: -1 },
	panelOffsetY: { type: Types.Float32, default: 0 },
});

type YButtonDragState = {
	hovered: boolean;
	dragging: boolean;
};

// Keep Y-button dragging outside IWSdk's ECS. Optional SDK components can be
// unregistered when their owning feature is disabled; querying one then throws
// from the ECS update loop and stops all XR rendering on-device.
const yButtonDragTargets = new Map<Entity, YButtonDragState>();

export class DraggablePanel {
	public entity: Entity;
	public panelEntity: Entity;
	private dragSurfaceGeometry: PlaneGeometry | null = null;
	private dragSurfaceMaterial: MeshBasicMaterial | null = null;
	private viewerPosition = new Vector3();
	private panelWorldPosition = new Vector3();
	private lookAtTarget = new Vector3();

	constructor(
		protected world: World,
		configPath: string,
		options: {
			maxWidth?: number;
			maxHeight?: number;
			yButtonDrag?: boolean;
			[key: string]: unknown;
		} = {},
	) {
		const width = options.maxWidth || 0.8;
		const height = options.maxHeight || 0.6;
		const yButtonDrag = options.yButtonDrag === true;
		const { yButtonDrag: _yButtonDrag, ...panelOptions } = options;
		const handleHeight = 0.05;
		const gap = 0.02;

		// 1. Create Handle (Root) - Interactable and Grabbable
		// Pre-add Visibility component to enable safe toggling at runtime
		this.entity = world
			.createTransformEntity()
			.addComponent(Interactable)
			.addComponent(Visibility, { isVisible: true });
		if (yButtonDrag) {
			yButtonDragTargets.set(this.entity, {
				hovered: false,
				dragging: false,
			});
		} else {
			this.entity.addComponent(DistanceGrabbable, {
				movementMode: MovementMode.MoveFromTarget,
			});
		}

		// Handle Visuals - Styling aligned with uikit panel
		const handleWidth = width * 0.5;
		const handleGeo = new BoxGeometry(handleWidth, handleHeight, 0.05);
		const handleMat = new MeshBasicMaterial({
			color: 0xe4e4e7, // Light grey
			transparent: true,
			opacity: 0.5,
		});
		const handleMesh = new Mesh(handleGeo, handleMat);
		if (this.entity.object3D) {
			this.entity.object3D.add(handleMesh);
		}

		// Panel Y - height/2 = handleHeight/2 + gap
		const panelY = height / 2 + handleHeight / 2 + gap;

		if (yButtonDrag && this.entity.object3D) {
			this.dragSurfaceGeometry = new PlaneGeometry(width, height);
			this.dragSurfaceMaterial = new MeshBasicMaterial({
				transparent: true,
				opacity: 0,
				depthWrite: false,
			});
			const dragSurface = new Mesh(
				this.dragSurfaceGeometry,
				this.dragSurfaceMaterial,
			);
			dragSurface.name = "camera-panel-y-drag-surface";
			dragSurface.position.y = panelY;
			this.entity.object3D.add(dragSurface);
		}

		// 2. Create Panel (Child) - Interactable but NOT Grabbable
		this.panelEntity = world.createTransformEntity().addComponent(PanelUI, {
			config: configPath,
			...panelOptions,
		});

		// NOTE: We do NOT parent the panel to the handle entity.
		// Instead, we link them via ID and sync their transforms in the system.
		// This prevents "grabbing the panel" from triggering "grabbing the handle".

		GlobalRefs.panelEntities.set(this.panelEntity.index, this.panelEntity);

		this.entity.addComponent(PanelHandle, {
			originalPosZ: handleMesh.position.z,
			originalScaleX: handleMesh.scale.x,
			originalScaleY: handleMesh.scale.y,
			originalScaleZ: handleMesh.scale.z,
			originalColorR: handleMat.color.r,
			originalColorG: handleMat.color.g,
			originalColorB: handleMat.color.b,
			panelEntityId: this.panelEntity.index,
			panelOffsetY: panelY,
		});
	}

	setPosition(x: number, y: number, z: number) {
		if (this.entity.object3D) {
			this.entity.object3D.position.set(x, y, z);
		}
	}

	faceUser() {
		if (this.entity.object3D) {
			const head = this.world.camera;
			if (head) {
				head.getWorldPosition(this.viewerPosition);
				faceViewerUpright(
					this.entity.object3D,
					this.viewerPosition,
					this.panelWorldPosition,
					this.lookAtTarget,
				);
			}
		}
	}

	dispose() {
		yButtonDragTargets.delete(this.entity);
		this.dragSurfaceGeometry?.dispose();
		this.dragSurfaceMaterial?.dispose();
		this.dragSurfaceGeometry = null;
		this.dragSurfaceMaterial = null;
		if (this.panelEntity) {
			GlobalRefs.panelEntities.delete(this.panelEntity.index);
			if (typeof this.panelEntity.destroy === "function") {
				this.panelEntity.destroy();
			}
		}
		if (this.entity && typeof this.entity.destroy === "function") {
			this.entity.destroy();
		}
	}
}

export class CameraPanel extends DraggablePanel {
	private videoMesh: Mesh | null = null;
	private videoElement: HTMLVideoElement | null = null;
	private _hasVideoTrack = false;

	constructor(world: World) {
		super(world, "./ui/camera.json", {
			maxHeight: 0.6,
			maxWidth: 0.8,
			yButtonDrag: true,
		});
	}

	public hasVideoTrack(): boolean {
		return this._hasVideoTrack;
	}

	setLabel(text: string) {
		if (this.panelEntity.hasComponent(CameraPanelInfo)) {
			this.panelEntity.setValue(CameraPanelInfo, "label", text);
		} else {
			this.panelEntity.addComponent(CameraPanelInfo, { label: text });
		}
	}

	dispose() {
		this.clearVideoTrack();
		super.dispose();
	}

	setVideoTrack(track: MediaStreamTrack) {
		this.clearVideoTrack();

		this._hasVideoTrack = true;
		const stream = new MediaStream([track]);
		this.videoElement = document.createElement("video");
		this.videoElement.srcObject = stream;
		this.videoElement.playsInline = true;
		this.videoElement.muted = true; // Required for autoplay
		this.videoElement.style.display = "none";
		document.body.appendChild(this.videoElement);

		this.videoElement.play().catch((e) => {
			console.error(`Video play error: ${e}`);
		});

		const texture = new VideoTexture(this.videoElement);
		// Aspect ratio adjusted to fit panel
		const geometry = new PlaneGeometry(0.76, 0.45);
		const material = new MeshBasicMaterial({ map: texture, side: DoubleSide });
		this.videoMesh = new Mesh(geometry, material);

		// Position it slightly in front of the panel to avoid z-fighting
		this.videoMesh.position.z = 0.01;
		// Adjust y to be centered below the header
		this.videoMesh.position.y = -0.05;

		// Attach to panelEntity, not the handle/root
		if (this.panelEntity.object3D) {
			this.panelEntity.object3D.add(this.videoMesh);
		}
	}

	private clearVideoTrack() {
		if (this.videoElement) {
			this.videoElement.pause();
			this.videoElement.srcObject = null;
			this.videoElement.remove();
			this.videoElement = null;
		}

		if (this.videoMesh) {
			this.videoMesh.geometry.dispose();
			if (Array.isArray(this.videoMesh.material)) {
				this.videoMesh.material.forEach((material) => {
					const texturedMaterial = material as MaterialWithMap;
					texturedMaterial.map?.dispose();
					texturedMaterial.dispose();
				});
			} else {
				const material = this.videoMesh.material as MaterialWithMap;
				material.map?.dispose();
				material.dispose();
			}
			if (this.panelEntity?.object3D) {
				this.panelEntity.object3D.remove(this.videoMesh);
			}
			this.videoMesh = null;
		}

		this._hasVideoTrack = false;
	}
}

export class ControllerCameraPanel {
	public entity: Entity;
	public handedness: "left" | "right";
	private videoMesh: Mesh | null = null;
	private videoElement: HTMLVideoElement | null = null;
	private _hasVideoTrack = false;

	constructor(world: World, handedness: "left" | "right") {
		this.handedness = handedness;

		// Create a simple transform entity (no grabbable, no panel UI)
		this.entity = world.createTransformEntity();

		// Create a background plane for the video
		const bgGeo = new PlaneGeometry(0.2, 0.15);
		const bgMat = new MeshBasicMaterial({
			color: 0x1a1a1a,
			transparent: true,
			opacity: 0.9,
			depthWrite: false,
			side: DoubleSide,
		});
		const bgMesh = new Mesh(bgGeo, bgMat);
		bgMesh.position.z = -0.002;
		bgMesh.renderOrder = 0;
		if (this.entity.object3D) {
			this.entity.object3D.add(bgMesh);
		}

		// Default to hidden if no track
		if (this.entity.object3D) {
			this.entity.object3D.visible = false;
		}
	}

	public hasVideoTrack(): boolean {
		return this._hasVideoTrack;
	}

	dispose() {
		this.clearVideoTrack();
		if (this.entity.object3D) {
			for (const child of [...this.entity.object3D.children]) {
				if (child instanceof Mesh) {
					child.geometry.dispose();
					if (Array.isArray(child.material)) {
						child.material.forEach((material) => material.dispose());
					} else {
						child.material.dispose();
					}
				}
			}
		}
		if (typeof this.entity.destroy === "function") {
			this.entity.destroy();
		}
	}

	setVideoTrack(track: MediaStreamTrack) {
		this.clearVideoTrack();

		this._hasVideoTrack = true;
		if (this.entity.object3D) {
			this.entity.object3D.visible = true;
		}

		const stream = new MediaStream([track]);
		this.videoElement = document.createElement("video");
		this.videoElement.srcObject = stream;
		this.videoElement.playsInline = true;
		this.videoElement.muted = true;
		this.videoElement.style.display = "none";
		document.body.appendChild(this.videoElement);

		this.videoElement.play().catch((e) => {
			console.error(`Video play error: ${e}`);
		});

		const texture = new VideoTexture(this.videoElement);
		const geometry = new PlaneGeometry(0.18, 0.135); // Slightly smaller than bg
		const material = new MeshBasicMaterial({ map: texture, side: DoubleSide });
		this.videoMesh = new Mesh(geometry, material);
		this.videoMesh.position.z = 0.001; // Slightly in front of bg
		this.videoMesh.renderOrder = 1;
		if (this.entity.object3D) {
			this.entity.object3D.add(this.videoMesh);
		}
	}

	private clearVideoTrack() {
		if (this.videoElement) {
			this.videoElement.pause();
			this.videoElement.srcObject = null;
			this.videoElement.remove();
			this.videoElement = null;
		}

		if (this.videoMesh) {
			this.videoMesh.geometry.dispose();
			if (Array.isArray(this.videoMesh.material)) {
				this.videoMesh.material.forEach((material) => {
					const texturedMaterial = material as MaterialWithMap;
					texturedMaterial.map?.dispose();
					texturedMaterial.dispose();
				});
			} else {
				const material = this.videoMesh.material as MaterialWithMap;
				material.map?.dispose();
				material.dispose();
			}
			if (this.entity.object3D) {
				this.entity.object3D.remove(this.videoMesh);
			}
			this.videoMesh = null;
		}

		this._hasVideoTrack = false;
		if (this.entity.object3D) {
			this.entity.object3D.visible = false;
		}
	}
}

export class CameraPanelSystem extends createSystem({
	cameraPanels: {
		required: [PanelUI, PanelDocument, CameraPanelInfo],
		where: [eq(PanelUI, "config", "./ui/camera.json")],
	},
}) {
	init() {
		this.queries.cameraPanels.subscribe("qualify", (entity) => {
			const document = PanelDocument.data.document[
				entity.index
			] as UIKitDocument;
			const label = CameraPanelInfo.data.label[entity.index];
			if (document && label) {
				const el = document.getElementById("camera-label");
				if (el) {
					console.log(
						`[CameraPanelSystem] Setting label for entity ${entity.index} to: ${label}`,
					);
					el.setProperties({ text: label });
				}
			}
		});
	}
}

export class PanelHoverSystem extends createSystem({
	handles: {
		required: [PanelHandle, Interactable],
	},
}) {
	update(delta: number) {
		this.queries.handles.entities.forEach((entity) => {
			const isCurrentlyHovered = entity.hasComponent(Hovered);
			const yDragState = yButtonDragTargets.get(entity);
			const isYHovered = yDragState?.hovered === true;
			const isYDragging = yDragState?.dragging === true;
			let visualState = PanelHandle.data.visualState[entity.index];
			let cd = PanelHandle.data.cooldown[entity.index];

			if (isCurrentlyHovered || isYHovered || isYDragging) {
				visualState = 1;
				cd = 0.1; // Cooldown of 0.1s to prevent flicker
			} else if (cd > 0) {
				cd -= delta;
				if (cd <= 0) {
					visualState = 0;
					cd = 0;
				}
			} else {
				visualState = 0;
			}

			PanelHandle.data.visualState[entity.index] = visualState;
			PanelHandle.data.cooldown[entity.index] = cd;

			// Sync Panel Position
			const panelId = PanelHandle.data.panelEntityId[entity.index];
			const offsetY = PanelHandle.data.panelOffsetY[entity.index];

			if (panelId !== -1 && entity.object3D) {
				const panelEntity = GlobalRefs.panelEntities.get(panelId);
				if (panelEntity?.object3D) {
					panelEntity.object3D.position.copy(entity.object3D.position);
					panelEntity.object3D.quaternion.copy(entity.object3D.quaternion);
					// Apply local offset Y in rotated space (along local Up)
					panelEntity.object3D.translateY(offsetY);
				}
			}

			if (!entity.object3D) return;

			const mesh = entity.object3D.children.find(
				(c: Object3D) => (c as Mesh).isMesh,
			) as Mesh;
			if (!mesh) return;

			const mat = mesh.material as MeshBasicMaterial;
			const origZ = PanelHandle.data.originalPosZ[entity.index];
			const origSX = PanelHandle.data.originalScaleX[entity.index];
			const origSY = PanelHandle.data.originalScaleY[entity.index];
			const origSZ = PanelHandle.data.originalScaleZ[entity.index];
			const origR = PanelHandle.data.originalColorR[entity.index];
			const origG = PanelHandle.data.originalColorG[entity.index];
			const origB = PanelHandle.data.originalColorB[entity.index];

			if (isYDragging) {
				mesh.position.z = origZ + 0.04;
				mesh.scale.set(origSX * 1.2, origSY * 1.2, origSZ * 1.2);
				mat.color.setRGB(0.1, 0.8, 0.35);
			} else if (isYHovered) {
				mesh.position.z = origZ + 0.04;
				mesh.scale.set(origSX * 1.2, origSY * 1.2, origSZ * 1.2);
				mat.color.setRGB(0.15, 0.45, 1);
			} else if (visualState) {
				mesh.position.z = origZ + 0.04;
				mesh.scale.set(origSX * 1.2, origSY * 1.2, origSZ * 1.2);
				mat.color.setRGB(origR * 0.4, origG * 0.4, origB * 0.4);
			} else {
				mesh.position.z = origZ;
				mesh.scale.set(origSX, origSY, origSZ);
				mat.color.setRGB(origR, origG, origB);
			}
		});
	}
}

const Y_BUTTON_INDEX = 5;
const B_BUTTON_INDEX = 5;

export class YButtonPanelDragSystem extends createSystem({}) {
	private raycaster = new Raycaster();
	private origin = new Vector3();
	private direction = new Vector3();
	private rayQuaternion = new Quaternion();
	private dragOffset = new Vector3();
	private targetPosition = new Vector3();
	private viewerPosition = new Vector3();
	private panelWorldPosition = new Vector3();
	private lookAtTarget = new Vector3();
	private activeEntity: Entity | null = null;
	private dragDistance = 0;
	private previousYPressed = false;
	private disabled = false;

	private buttonPressed(
		handedness: "left" | "right",
		componentId: string,
		fallbackIndex: number,
	): boolean {
		return isControllerButtonPressed(
			this.input?.gamepads?.[handedness],
			componentId,
			fallbackIndex,
		);
	}

	private getLeftRaySpace(): Object3D | null {
		return resolveControllerSpace(
			this.world.player,
			this.input,
			"left",
			"ray",
		);
	}

	private updateRay(raySpace: Object3D): void {
		raySpace.getWorldPosition(this.origin);
		raySpace.getWorldQuaternion(this.rayQuaternion);
		this.direction
			.set(0, 0, -1)
			.applyQuaternion(this.rayQuaternion)
			.normalize();
		this.raycaster.set(this.origin, this.direction);
	}

	private findTarget(): {
		entity: Entity;
		distance: number;
		point: Vector3;
	} | null {
		let nearest: {
			entity: Entity;
			distance: number;
			point: Vector3;
		} | null = null;

		for (const [entity, state] of yButtonDragTargets) {
			state.hovered = false;
			if (!entity.object3D?.visible) continue;
			const hit = this.raycaster.intersectObject(entity.object3D, true)[0];
			if (!hit || (nearest && hit.distance >= nearest.distance)) continue;
			nearest = {
				entity,
				distance: hit.distance,
				point: hit.point.clone(),
			};
		}

		if (nearest) {
			const state = yButtonDragTargets.get(nearest.entity);
			if (state) state.hovered = true;
		}
		return nearest;
	}

	private endDrag(): void {
		if (this.activeEntity) {
			const state = yButtonDragTargets.get(this.activeEntity);
			if (state) state.dragging = false;
		}
		this.activeEntity = null;
	}

	update() {
		if (this.disabled) return;

		try {
			this.updateYDrag();
		} catch (error) {
			// A controller implementation difference must never stop the shared
			// XR animation loop. Disable only this optional interaction.
			this.endDrag();
			this.disabled = true;
			console.error(
				"[YButtonPanelDragSystem] Disabled after controller API error:",
				error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : error,
			);
		}
	}

	private updateYDrag() {
		const yPressed = this.buttonPressed(
			"left",
			ControllerButton.secondaryLeft,
			Y_BUTTON_INDEX,
		);
		const bPressed = this.buttonPressed(
			"right",
			ControllerButton.secondaryRight,
			B_BUTTON_INDEX,
		);
		const teleopEngaged = useAppStore.getState().teleopEngaged;
		const raySpace = this.getLeftRaySpace();

		if (!raySpace) {
			this.endDrag();
			this.previousYPressed = yPressed;
			return;
		}

		this.updateRay(raySpace);
		const hit = this.findTarget();

		if (
			this.activeEntity &&
			shouldEndYDrag(yPressed, bPressed, teleopEngaged)
		) {
			this.endDrag();
		}

		if (
			!this.activeEntity &&
			shouldBeginYDrag({
				yPressed,
				previousYPressed: this.previousYPressed,
				bPressed,
				teleopEngaged,
				hasTarget: hit !== null,
			}) &&
			hit
		) {
			this.activeEntity = hit.entity;
			this.dragDistance = hit.distance;
			hit.entity.object3D?.getWorldPosition(this.targetPosition);
			this.dragOffset.copy(this.targetPosition).sub(hit.point);
			const state = yButtonDragTargets.get(hit.entity);
			if (state) state.dragging = true;
		}

		const object = this.activeEntity?.object3D;
		if (object) {
			this.targetPosition
				.copy(this.direction)
				.multiplyScalar(this.dragDistance)
				.add(this.origin)
				.add(this.dragOffset);
			if (object.parent) {
				object.parent.worldToLocal(this.targetPosition);
			}
			object.position.copy(this.targetPosition);

			// Reorient after moving so the panel's front keeps facing the
			// operator wherever it is placed. The final orientation remains
			// when Y is released.
			const viewer = this.world.player?.head ?? this.world.camera;
			if (viewer) {
				viewer.getWorldPosition(this.viewerPosition);
				faceViewerUpright(
					object,
					this.viewerPosition,
					this.panelWorldPosition,
					this.lookAtTarget,
				);
			}
		}

		this.previousYPressed = yPressed;
	}
}

export class PanelDragLockSystem extends createSystem({
	handles: {
		required: [PanelHandle],
	},
}) {
	update() {
		const teleopEngaged = useAppStore.getState().teleopEngaged;

		for (const entity of this.queries.handles.entities) {
			const usesYButton = yButtonDragTargets.has(entity);
			// A Y-drag camera never installs DistanceGrabbable. With the SDK
			// grabbing feature disabled, even querying that unregistered
			// component raises inside the ECS bitmask implementation.
			const hasGrab =
				!usesYButton && entity.hasComponent(DistanceGrabbable);
			const hasInteractable = entity.hasComponent(Interactable);

			if (teleopEngaged) {
				if (hasGrab) {
					entity.removeComponent(DistanceGrabbable);
				}
				if (hasInteractable) {
					entity.removeComponent(Interactable);
				}
				continue;
			}

			if (!hasInteractable) {
				entity.addComponent(Interactable);
			}
			if (!usesYButton && !hasGrab) {
				entity.addComponent(DistanceGrabbable, {
					movementMode: MovementMode.MoveFromTarget,
				});
			}
		}
	}
}
