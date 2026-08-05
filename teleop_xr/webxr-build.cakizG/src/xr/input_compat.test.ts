import { describe, expect, it, vi } from "vitest";
import {
	ControllerButton,
	isControllerButtonPressed,
	resolveControllerSpace,
} from "./input_compat";

describe("PICO controller compatibility", () => {
	it("uses the pico-4u semantic Y mapping before the raw fallback", () => {
		const getButtonPressed = vi.fn(() => true);
		const controller = {
			buttonMapping: new Map([[ControllerButton.secondaryLeft, 5]]),
			getButtonPressed,
			gamepad: { buttons: Array.from({ length: 6 }, () => ({ pressed: false })) },
		};

		expect(
			isControllerButtonPressed(
				controller,
				ControllerButton.secondaryLeft,
				5,
			),
		).toBe(true);
		expect(getButtonPressed).toHaveBeenCalledWith(
			ControllerButton.secondaryLeft,
		);
	});

	it("keeps button index 5 as a PICO 4/Ultra compatibility fallback", () => {
		const controller = {
			gamepad: {
				buttons: Array.from({ length: 6 }, (_, index) => ({
					pressed: index === 5,
				})),
			},
		};

		expect(
			isControllerButtonPressed(
				controller,
				ControllerButton.secondaryRight,
				5,
			),
		).toBe(true);
	});

	it("uses secondary controller spaces when hands are primary", () => {
		const primary = { name: "hand-space" };
		const secondary = { name: "controller-space" };
		const input = { isPrimary: () => false };
		const player = {
			raySpaces: { left: primary },
			secondaryRaySpaces: { left: secondary },
		};

		expect(
			resolveControllerSpace(player as never, input, "left", "ray"),
		).toBe(secondary);
	});
});
