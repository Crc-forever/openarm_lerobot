import type { Object3D } from "three";

export type ControllerHandedness = "left" | "right";
export type ControllerSpaceKind = "grip" | "ray";

export const ControllerButton = {
	trigger: "xr-standard-trigger",
	squeeze: "xr-standard-squeeze",
	thumbstick: "xr-standard-thumbstick",
	primaryLeft: "x-button",
	secondaryLeft: "y-button",
	primaryRight: "a-button",
	secondaryRight: "b-button",
	menu: "menu",
} as const;

type RawButton = {
	pressed?: boolean;
};

export type StatefulGamepadLike = {
	gamepad?: {
		buttons?: ArrayLike<RawButton>;
		axes?: ArrayLike<number>;
		mapping?: string;
	};
	inputSource?: {
		profiles?: readonly string[];
	};
	buttonMapping?: {
		has: (componentId: string) => boolean;
		get?: (componentId: string) => number | undefined;
	};
	getButtonPressed?: (componentId: string) => boolean;
};

type PlayerSpacesLike = {
	gripSpaces?: Partial<Record<ControllerHandedness, Object3D>>;
	secondaryGripSpaces?: Partial<Record<ControllerHandedness, Object3D>>;
	raySpaces?: Partial<Record<ControllerHandedness, Object3D>>;
	secondaryRaySpaces?: Partial<Record<ControllerHandedness, Object3D>>;
};

type InputManagerLike = {
	isPrimary?: (
		deviceType: "controller" | "hand",
		handedness: ControllerHandedness,
	) => boolean;
};

/**
 * Prefer the semantic mapping resolved from the WebXR input profile
 * (`pico-4u`, `pico-4`, etc.). Raw xr-standard indices remain a fallback for
 * runtimes which expose a gamepad before its profile has finished resolving.
 */
export function isControllerButtonPressed(
	controller: StatefulGamepadLike | undefined,
	componentId: string,
	fallbackIndex: number,
): boolean {
	if (!controller) return false;

	if (
		controller.buttonMapping?.has(componentId) &&
		typeof controller.getButtonPressed === "function"
	) {
		return Boolean(controller.getButtonPressed(componentId));
	}

	const buttons = controller.gamepad?.buttons;
	return Boolean(
		buttons &&
			buttons.length > fallbackIndex &&
			buttons[fallbackIndex]?.pressed,
	);
}

/**
 * Resolve the physical controller space even when a runtime exposes hands and
 * controllers simultaneously and moves the controller into secondary spaces.
 */
export function resolveControllerSpace(
	player: PlayerSpacesLike | undefined,
	input: InputManagerLike | undefined,
	handedness: ControllerHandedness,
	kind: ControllerSpaceKind,
): Object3D | null {
	if (!player) return null;

	const primary =
		kind === "grip"
			? player.gripSpaces?.[handedness]
			: player.raySpaces?.[handedness];
	const secondary =
		kind === "grip"
			? player.secondaryGripSpaces?.[handedness]
			: player.secondaryRaySpaces?.[handedness];

	let controllerIsPrimary: boolean | undefined;
	try {
		const result = input?.isPrimary?.("controller", handedness);
		if (typeof result === "boolean") controllerIsPrimary = result;
	} catch (error) {
		console.warn(
			`[XRInputCompat] Unable to resolve ${handedness} controller priority:`,
			error,
		);
	}

	return (
		(controllerIsPrimary === false
			? secondary ?? primary
			: primary ?? secondary) ?? null
	);
}

export function describeController(
	controller: StatefulGamepadLike | undefined,
): {
	profiles: string[];
	mapping: string;
	buttonCount: number;
	axesCount: number;
	semanticButtons: string[];
} | null {
	if (!controller) return null;

	const profiles = Array.from(controller.inputSource?.profiles ?? []);
	const rawGamepad = controller.gamepad;
	const knownComponents = Object.values(ControllerButton);

	return {
		profiles,
		mapping: rawGamepad?.mapping ?? "",
		buttonCount: rawGamepad?.buttons?.length ?? 0,
		axesCount: rawGamepad?.axes?.length ?? 0,
		semanticButtons: knownComponents.filter((id) =>
			controller.buttonMapping?.has(id),
		),
	};
}
