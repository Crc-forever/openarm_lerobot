import { describe, expect, it } from "vitest";
import { shouldBeginYDrag, shouldEndYDrag } from "./panel_y_drag_logic";

describe("Y-button panel drag gating", () => {
	it("starts only on a fresh Y press over a target", () => {
		expect(
			shouldBeginYDrag({
				yPressed: true,
				previousYPressed: false,
				bPressed: false,
				teleopEngaged: false,
				hasTarget: true,
			}),
		).toBe(true);
		expect(
			shouldBeginYDrag({
				yPressed: true,
				previousYPressed: true,
				bPressed: false,
				teleopEngaged: false,
				hasTarget: true,
			}),
		).toBe(false);
		expect(
			shouldBeginYDrag({
				yPressed: true,
				previousYPressed: false,
				bPressed: false,
				teleopEngaged: false,
				hasTarget: false,
			}),
		).toBe(false);
	});

	it("yields to Y+B reset and active teleoperation", () => {
		expect(shouldEndYDrag(true, true, false)).toBe(true);
		expect(shouldEndYDrag(true, false, true)).toBe(true);
		expect(shouldEndYDrag(true, false, false)).toBe(false);
	});
});
