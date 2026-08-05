import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VideoClient } from "./video";

type SentMessage = {
	type: string;
	client_id: string;
	data?: unknown;
};

class MockWebSocket {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSING = 2;
	static readonly CLOSED = 3;
	static instances: MockWebSocket[] = [];

	readonly sent: string[] = [];
	readyState = MockWebSocket.CONNECTING;
	onopen: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onmessage: ((event: MessageEvent<string>) => void) | null = null;

	constructor(public readonly url: string) {
		MockWebSocket.instances.push(this);
	}

	send(payload: string) {
		this.sent.push(payload);
	}

	close() {
		this.readyState = MockWebSocket.CLOSED;
	}

	emitOpen() {
		this.readyState = MockWebSocket.OPEN;
		this.onopen?.({} as Event);
	}

	emitMessage(message: unknown) {
		this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent<string>);
	}

	emitClose(code = 1000, reason = "") {
		this.readyState = MockWebSocket.CLOSED;
		this.onclose?.({ code, reason } as CloseEvent);
	}
}

class MockPeerConnection {
	static instances: MockPeerConnection[] = [];

	connectionState: RTCPeerConnectionState = "new";
	localDescription: RTCSessionDescriptionInit | null = null;
	ontrack: ((event: RTCTrackEvent) => void) | null = null;
	onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null;
	onconnectionstatechange: (() => void) | null = null;

	constructor() {
		MockPeerConnection.instances.push(this);
	}

	async setRemoteDescription(_description: RTCSessionDescriptionInit) {}

	async createAnswer(): Promise<RTCSessionDescriptionInit> {
		return { type: "answer", sdp: "answer-sdp" };
	}

	async setLocalDescription(description: RTCSessionDescriptionInit) {
		this.localDescription = description;
	}

	async addIceCandidate(_candidate: RTCIceCandidateInit) {}

	async getStats(): Promise<RTCStatsReport> {
		return new Map() as RTCStatsReport;
	}

	getReceivers(): RTCRtpReceiver[] {
		return [];
	}

	close() {
		this.connectionState = "closed";
	}

	emitConnectionState(state: RTCPeerConnectionState) {
		this.connectionState = state;
		this.onconnectionstatechange?.();
	}

	emitTrack(trackId: string) {
		const receiver = { jitterBufferTarget: null };
		this.ontrack?.({
			track: { kind: "video", id: trackId } as MediaStreamTrack,
			streams: [{ id: trackId }] as MediaStream[],
			transceiver: { mid: trackId } as RTCRtpTransceiver,
			receiver,
		} as unknown as RTCTrackEvent);
		return receiver;
	}
}

const parseSent = (socket: MockWebSocket): SentMessage[] =>
	socket.sent.map((payload) => JSON.parse(payload) as SentMessage);

const flushMicrotasks = async () => {
	await Promise.resolve();
	await Promise.resolve();
};

describe("VideoClient", () => {
	beforeEach(() => {
		MockWebSocket.instances = [];
		MockPeerConnection.instances = [];
		vi.useFakeTimers();
		vi.stubGlobal("window", globalThis);
		vi.stubGlobal("WebSocket", MockWebSocket);
		vi.stubGlobal("RTCPeerConnection", MockPeerConnection);
		vi.stubGlobal("crypto", { randomUUID: () => "client-1" });
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it("reconnects its websocket and requests video again after close", () => {
		new VideoClient("wss://example.test/ws", vi.fn(), vi.fn());

		const firstSocket = MockWebSocket.instances[0];
		firstSocket.emitOpen();
		firstSocket.emitMessage({
			type: "control_status",
			data: { in_control: true },
		});

		expect(parseSent(firstSocket).map((message) => message.type)).toEqual([
			"control_check",
			"video_request",
		]);

		firstSocket.emitClose(1006, "network lost");
		vi.advanceTimersByTime(3000);

		expect(MockWebSocket.instances).toHaveLength(2);
		const secondSocket = MockWebSocket.instances[1];
		secondSocket.emitOpen();
		secondSocket.emitMessage({
			type: "control_status",
			data: { in_control: true },
		});

		expect(parseSent(secondSocket).map((message) => message.type)).toEqual([
			"control_check",
			"video_request",
		]);
	});

	it("does not reconnect after disposal", () => {
		const client = new VideoClient(
			"wss://example.test/ws",
			vi.fn(),
			vi.fn(),
		);

		const socket = MockWebSocket.instances[0];
		socket.emitOpen();
		client.dispose();
		vi.advanceTimersByTime(30000);

		expect(socket.readyState).toBe(MockWebSocket.CLOSED);
		expect(MockWebSocket.instances).toHaveLength(1);
	});

	it("resets per-offer track indexes after reconnecting", async () => {
		const trackEvents: Array<{ trackId: string; trackIndex: number }> = [];
		new VideoClient(
			"wss://example.test/ws",
			vi.fn(),
			(_track, trackId, trackIndex) => {
				trackEvents.push({ trackId, trackIndex });
			},
		);

		const firstSocket = MockWebSocket.instances[0];
		firstSocket.emitOpen();
		firstSocket.emitMessage({
			type: "video_offer",
			data: { type: "offer", sdp: "offer-1" },
		});
		await flushMicrotasks();

		const firstPeer = MockPeerConnection.instances[0];
		const headReceiver = firstPeer.emitTrack("head");
		firstPeer.emitTrack("wrist_left");
		expect(headReceiver.jitterBufferTarget).toBe(0);

		firstSocket.emitClose(1006, "network lost");
		vi.advanceTimersByTime(3000);

		const secondSocket = MockWebSocket.instances[1];
		secondSocket.emitOpen();
		secondSocket.emitMessage({
			type: "video_offer",
			data: { type: "offer", sdp: "offer-2" },
		});
		await flushMicrotasks();

		const secondPeer = MockPeerConnection.instances[1];
		secondPeer.emitTrack("head");

		expect(trackEvents).toEqual([
			{ trackId: "head", trackIndex: 0 },
			{ trackId: "wrist_left", trackIndex: 1 },
			{ trackId: "head", trackIndex: 0 },
		]);
	});

	it("keeps the same peer across a transient ICE disconnection", async () => {
		new VideoClient("wss://example.test/ws", vi.fn(), vi.fn());

		const socket = MockWebSocket.instances[0];
		socket.emitOpen();
		socket.emitMessage({
			type: "control_status",
			data: { in_control: true },
		});
		socket.emitMessage({
			type: "video_offer",
			data: { type: "offer", sdp: "offer-1" },
		});
		await flushMicrotasks();

		const peer = MockPeerConnection.instances[0];
		const videoRequestsBefore = parseSent(socket).filter(
			(message) => message.type === "video_request",
		).length;
		peer.emitConnectionState("disconnected");
		vi.advanceTimersByTime(4000);
		peer.emitConnectionState("connected");
		vi.advanceTimersByTime(10000);

		expect(MockPeerConnection.instances).toHaveLength(1);
		expect(peer.connectionState).toBe("connected");
		expect(
			parseSent(socket).filter((message) => message.type === "video_request"),
		).toHaveLength(videoRequestsBefore);
	});
});
