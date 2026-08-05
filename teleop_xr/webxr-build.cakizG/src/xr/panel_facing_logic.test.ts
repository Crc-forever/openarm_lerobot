import { Object3D, Vector3 } from "three";
import { describe, expect, it } from "vitest";
import { faceViewerUpright } from "./panel_facing_logic";

describe("upright panel facing", () => {
	it("faces an elevated viewer without tilting the panel upward", () => {
		const panel = new Object3D();
		panel.position.set(0.55, 1.15, -1.5);

		faceViewerUpright(
			panel,
			new Vector3(0, 1.7, 0),
			new Vector3(),
			new Vector3(),
		);

		const forward = new Vector3(0, 0, 1).applyQuaternion(panel.quaternion);
		const expected = new Vector3(-0.55, 0, 1.5).normalize();
		expect(forward.x).toBeCloseTo(expected.x);
		expect(forward.y).toBeCloseTo(0);
		expect(forward.z).toBeCloseTo(expected.z);
		expect(panel.rotation.z).toBeCloseTo(0);
	});

	it("does not change orientation at a degenerate horizontal position", () => {
		const panel = new Object3D();
		panel.rotation.set(0, 0.4, 0);
		const before = panel.quaternion.clone();

		faceViewerUpright(
			panel,
			new Vector3(0, 2, 0),
			new Vector3(),
			new Vector3(),
		);

		expect(panel.quaternion.equals(before)).toBe(true);
	});
});
