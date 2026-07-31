import { getClientId } from "../client_id";
import { useAppStore } from "../lib/store";

/**
 * Console log streaming utility for Quest VR debugging.
 * Intercepts console.log/warn/error and sends to Python server via WebSocket.
 */

let ws: WebSocket | null = null;
let messageQueue: string[] = [];
let currentLogLevel: "info" | "warn" | "error" = "info";
let initialized = false;
let disposed = false;
let reconnectTimer: number | null = null;

const LOG_LEVEL_PRIORITY: Record<string, number> = {
	log: 0,
	info: 0,
	warn: 1,
	error: 2,
};

const LOG_THRESHOLD: Record<string, number> = {
	info: 0,
	warn: 1,
	error: 2,
};

const clientId = getClientId();
const originalConsole = {
	log: console.log.bind(console),
	warn: console.warn.bind(console),
	error: console.error.bind(console),
	info: console.info.bind(console),
};

// biome-ignore lint/suspicious/noExplicitAny: Console args are any
function sendLog(level: string, args: any[]) {
	const levelPriority = LOG_LEVEL_PRIORITY[level] ?? 0;
	const threshold = LOG_THRESHOLD[currentLogLevel] ?? 0;

	if (levelPriority < threshold) {
		return;
	}

	const message = args
		.map((arg) => (typeof arg === "object" ? JSON.stringify(arg) : String(arg)))
		.join(" ");

	const payload = JSON.stringify({
		type: "console_log",
		client_id: clientId,
		data: { level, message },
	});

	if (ws && ws.readyState === WebSocket.OPEN) {
		ws.send(payload);
	} else {
		// Queue messages until connected
		messageQueue.push(payload);
	}
}

function connectConsoleStream() {
	if (disposed || ws) return;
	const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
	const wsUrl = `${protocol}//${window.location.host}/ws`;

	const socket = new WebSocket(wsUrl);
	ws = socket;

	socket.onopen = () => {
		if (disposed || ws !== socket) {
			socket.close();
			return;
		}
		// Flush queued messages
		for (const msg of messageQueue) {
			socket.send(msg);
		}
		messageQueue = [];
		originalConsole.log("[ConsoleStream] Connected");
	};

	socket.onclose = () => {
		if (ws === socket) {
			ws = null;
		}
		if (disposed) return;
		originalConsole.warn("[ConsoleStream] Disconnected, reconnecting...");
		if (reconnectTimer === null) {
			reconnectTimer = window.setTimeout(() => {
				reconnectTimer = null;
				connectConsoleStream();
			}, 3000);
		}
	};

	socket.onerror = (e) => {
		originalConsole.error("[ConsoleStream] Error", e);
	};
}

function shutdownConsoleStream() {
	disposed = true;
	if (reconnectTimer !== null) {
		window.clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}
	const socket = ws;
	ws = null;
	if (socket) {
		socket.onopen = null;
		socket.onclose = null;
		socket.onerror = null;
		if (
			socket.readyState === WebSocket.OPEN ||
			socket.readyState === WebSocket.CONNECTING
		) {
			socket.close();
		}
	}
}

export function initConsoleStream() {
	if (initialized) return;
	initialized = true;
	disposed = false;

	useAppStore.subscribe((state) => {
		currentLogLevel = state.advancedSettings.logLevel;
	});
	currentLogLevel = useAppStore.getState().advancedSettings.logLevel;

	// Intercept console methods
	// biome-ignore lint/suspicious/noExplicitAny: Console methods accept any arguments
	console.log = (...args: any[]) => {
		originalConsole.log(...args);
		sendLog("log", args);
	};

	// biome-ignore lint/suspicious/noExplicitAny: Console methods accept any arguments
	console.warn = (...args: any[]) => {
		originalConsole.warn(...args);
		sendLog("warn", args);
	};

	// biome-ignore lint/suspicious/noExplicitAny: Console methods accept any arguments
	console.error = (...args: any[]) => {
		originalConsole.error(...args);
		sendLog("error", args);
	};

	// biome-ignore lint/suspicious/noExplicitAny: Console methods accept any arguments
	console.info = (...args: any[]) => {
		originalConsole.info(...args);
		sendLog("info", args);
	};

	window.addEventListener("pagehide", shutdownConsoleStream, { once: true });
	connectConsoleStream();
}
