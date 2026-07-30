import type { Object3D, Vector3 } from "three";

/**
 * Point a panel at the viewer without pitching or rolling it.
 *
 * A floating panel is usually below or beside the headset. Looking directly at
 * the headset position would therefore tilt the panel upward, which makes an
 * otherwise correctly aimed panel appear crooked. Projecting the viewer onto
 * the panel's horizontal plane keeps the UI upright while retaining the
 * correct left/right heading.
 */
export function faceViewerUpright(
	object: Object3D,
	viewerWorldPosition: Vector3,
	objectWorldPosition: Vector3,
	lookAtTarget: Vector3,
): void {
	object.getWorldPosition(objectWorldPosition);
	lookAtTarget.copy(viewerWorldPosition);
	lookAtTarget.y = objectWorldPosition.y;

	// Avoid an undefined lookAt rotation if the panel and viewer happen to
	// share the same horizontal position.
	const horizontalDistanceSquared =
		(lookAtTarget.x - objectWorldPosition.x) ** 2 +
		(lookAtTarget.z - objectWorldPosition.z) ** 2;
	if (horizontalDistanceSquared < 1e-8) return;

	object.lookAt(lookAtTarget);
}
