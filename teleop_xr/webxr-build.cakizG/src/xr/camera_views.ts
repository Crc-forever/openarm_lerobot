export type CameraView = {
	device: string;
};

export type CameraViewsConfig = Record<string, CameraView>;

let currentConfig: CameraViewsConfig = {};
const handlers = new Set<(config: CameraViewsConfig) => void>();

export function setCameraViewsConfig(config: CameraViewsConfig | null): void {
	currentConfig = config || {};
	console.log(
		"[CameraViews] setCameraViewsConfig called, keys:",
		Object.keys(currentConfig),
		"handlers:",
		handlers.size,
	);
	let index = 0;
	handlers.forEach((handler) => {
		console.log("[CameraViews] Calling handler", index);
		try {
			handler(currentConfig);
			console.log("[CameraViews] Handler", index, "completed");
		} catch (e) {
			console.error("[CameraViews] Handler", index, "threw error:", e);
		}
		index += 1;
	});
	console.log("[CameraViews] All handlers completed");
}

export function getCameraViewsConfig(): CameraViewsConfig {
	return currentConfig;
}

export function isViewEnabled(key: string): boolean {
	return !!currentConfig[key];
}

export function onCameraViewsChanged(
	handler: (config: CameraViewsConfig) => void,
): () => void {
	handlers.add(handler);
	console.log("[CameraViews] Handler added, total handlers:", handlers.size);
	handler(currentConfig);
	return () => {
		handlers.delete(handler);
	};
}
