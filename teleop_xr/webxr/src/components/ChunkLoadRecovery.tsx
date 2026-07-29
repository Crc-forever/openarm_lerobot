"use client";

import { useEffect } from "react";

const RECOVERY_KEY = "__teleop_xr_chunk_recovery__";

function isChunkLoadFailure(value: unknown): boolean {
	const text =
		value instanceof Error
			? `${value.name} ${value.message}`
			: typeof value === "string"
				? value
				: String(value ?? "");

	return /ChunkLoadError|Failed to load chunk|Loading chunk .* failed/i.test(text);
}

function reloadOnce() {
	if (sessionStorage.getItem(RECOVERY_KEY) === "1") return;
	sessionStorage.setItem(RECOVERY_KEY, "1");

	const url = new URL(window.location.href);
	url.searchParams.set("_asset_refresh", Date.now().toString(36));
	window.location.replace(url);
}

export function ChunkLoadRecovery() {
	useEffect(() => {
		// A successful hydration clears the one-shot guard so a future deploy
		// can recover as well.
		const clearGuard = window.setTimeout(() => {
			sessionStorage.removeItem(RECOVERY_KEY);
		}, 10_000);

		const onError = (event: ErrorEvent) => {
			if (isChunkLoadFailure(event.error ?? event.message)) reloadOnce();
		};
		const onUnhandledRejection = (event: PromiseRejectionEvent) => {
			if (isChunkLoadFailure(event.reason)) reloadOnce();
		};

		window.addEventListener("error", onError);
		window.addEventListener("unhandledrejection", onUnhandledRejection);
		return () => {
			window.clearTimeout(clearGuard);
			window.removeEventListener("error", onError);
			window.removeEventListener("unhandledrejection", onUnhandledRejection);
		};
	}, []);

	return null;
}
