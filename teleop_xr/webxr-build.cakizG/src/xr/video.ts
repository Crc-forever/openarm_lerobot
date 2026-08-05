import { getClientId } from "../client_id";

export type VideoStats = {
	state: string;
	streams: Array<{
		id: string;
		fps: number;
		bitrateKbps: number;
		width: number;
		height: number;
		packetsLost: number;
		jitter: number;
		jitterBufferMs: number;
		decodeMs: number;
		framesDropped: number;
	}>;
};

export class VideoClient {
	private ws: WebSocket | null = null;
	private pc: RTCPeerConnection | null = null;
	private statsTimer: number | null = null;
	private clientId = getClientId();
	private controlPollTimer: number | null = null;
	private waitingForOffer = false;
	private reconnectTimer: number | null = null;
	private peerDisconnectTimer: number | null = null;
	private reconnectAttempt = 0;
	private disposed = false;
	private previousInboundStats = new Map<
		string,
		{ bytesReceived: number; timestamp: number }
	>();

	constructor(
		private url: string,
		private onStats: (stats: VideoStats) => void,
		private onTrack?: (
			track: MediaStreamTrack,
			trackId: string,
			trackIndex: number,
		) => void,
	) {
		this.connectWebSocket();
	}

	private connectWebSocket() {
		if (this.disposed) return;

		const ws = new WebSocket(this.url);
		this.ws = ws;
		ws.onmessage = (event) => this.handleMessage(JSON.parse(event.data));
		ws.onopen = () => {
			if (this.disposed || this.ws !== ws) {
				ws.close();
				return;
			}
			console.log(`[VideoClient] WebSocket connected. URL: ${this.url}`);
			this.reconnectAttempt = 0;
			this.clearReconnectTimer();
			this.waitingForOffer = false;
			this.sendControlCheck();
			this.startControlPolling();
		};
		ws.onerror = (err) => {
			console.error("[VideoClient] WebSocket error:", err);
		};
		ws.onclose = (event) => {
			if (this.ws === ws) {
				this.ws = null;
			}
			console.warn(
				`[VideoClient] WebSocket closed: ${event.code} ${event.reason}`,
			);
			this.waitingForOffer = false;
			this.closePeerConnection();
			this.stopControlPolling();
			if (!this.disposed) {
				this.scheduleReconnect();
			}
		};
	}

	private async handleMessage(msg: { type: string; data: unknown }) {
		if (msg.type === "deny") {
			console.warn("[VideoClient] Received DENY:", msg.data);
			this.waitingForOffer = false;
			this.closePeerConnection();
			this.startControlPolling();
			return;
		}

		if (msg.type === "control_status") {
			// biome-ignore lint/suspicious/noExplicitAny: external message
			const inControl = Boolean((msg as any).data?.in_control);

			if (inControl) {
				// Keep polling as a lightweight control lease heartbeat. The
				// standalone viewer has no XR-state socket, so stopping here lets
				// background browser tabs steal its video session after 5 seconds.
				this.startControlPolling();
				if (!this.pc && !this.waitingForOffer) {
					const ws = this.ws;
					if (!ws || ws.readyState !== WebSocket.OPEN) {
						return;
					}
					console.log(
						`[VideoClient] In control, requesting video. ClientID: ${this.clientId}`,
					);
					this.waitingForOffer = true;
					ws.send(
						JSON.stringify({
							type: "video_request",
							client_id: this.clientId,
						}),
					);
				}
			} else {
				this.startControlPolling();
			}
			return;
		}

		if (msg.type === "video_offer") {
			console.log("[VideoClient] Received video offer");
			this.waitingForOffer = false;
			this.closePeerConnection();
			const peer = new RTCPeerConnection();
			this.pc = peer;
			let trackIndex = 0;
			peer.onconnectionstatechange = () => {
				if (this.pc !== peer) return;
				const state = peer.connectionState;
				if (state === "connected") {
					this.clearPeerDisconnectTimer();
					return;
				}
				if (state === "disconnected") {
					// Chromium reports transient disconnections while ICE changes
					// candidate pairs. Give the same peer time to recover instead of
					// restarting the camera and encoder immediately.
					if (this.peerDisconnectTimer === null) {
						this.peerDisconnectTimer = window.setTimeout(() => {
							this.peerDisconnectTimer = null;
							if (this.pc === peer && peer.connectionState === "disconnected") {
								this.restartPeerConnection("disconnected timeout");
							}
						}, 8000);
					}
					return;
				}
				if (state === "failed" || state === "closed") {
					console.warn(`[VideoClient] Peer connection state changed: ${state}`);
					this.restartPeerConnection(state);
				}
			};
			peer.ontrack = (event) => {
				// PICO Browser is Chromium-based. Ask its WebRTC receiver to
				// favor the newest frame over smooth playback of queued frames.
				// Keep the older Chromium name as a compatibility fallback.
				const lowLatencyReceiver = event.receiver as unknown as
					| Record<string, unknown>
					| undefined;
				try {
					if (
						lowLatencyReceiver &&
						"jitterBufferTarget" in lowLatencyReceiver
					) {
						lowLatencyReceiver.jitterBufferTarget = 0;
					} else if (
						lowLatencyReceiver &&
						"playoutDelayHint" in lowLatencyReceiver
					) {
						lowLatencyReceiver.playoutDelayHint = 0;
					}
				} catch (error) {
					console.warn(
						"[VideoClient] Browser rejected low-latency playout hint:",
						error,
					);
				}

				console.log(
					"[VideoClient] ontrack event:",
					"kind=",
					event.track.kind,
					"id=",
					event.track.id,
					"mid=",
					event.transceiver?.mid,
					"streams=",
					event.streams.map((s) => s.id),
				);

				if (this.onTrack && event.track.kind === "video") {
					// Prioritize stream ID if available (matches python stream_ids)
					let trackId = event.track.id;
					if (event.streams.length > 0) {
						trackId = event.streams[0].id;
					} else if (event.transceiver?.mid) {
						trackId = event.transceiver.mid;
					}

					if (trackId) {
						console.log(`[VideoClient] Notifying onTrack with ID: ${trackId}`);
						this.onTrack(event.track, trackId, trackIndex);
						trackIndex += 1;
					} else {
						console.warn("[VideoClient] Could not determine track ID");
					}
				}
			};
			peer.onicecandidate = (e) => {
				if (e.candidate) {
					const ws = this.ws;
					if (!ws || ws.readyState !== WebSocket.OPEN) {
						return;
					}
					ws.send(
						JSON.stringify({
							type: "video_ice",
							client_id: this.clientId,
							data: e.candidate,
						}),
					);
				}
			};
			try {
				await this.pc.setRemoteDescription(
					msg.data as RTCSessionDescriptionInit,
				);
				const answer = await this.pc.createAnswer();
				await this.pc.setLocalDescription(answer);
				const ws = this.ws;
				if (!ws || ws.readyState !== WebSocket.OPEN) {
					return;
				}
				ws.send(
					JSON.stringify({
						type: "video_answer",
						client_id: this.clientId,
						data: this.pc.localDescription,
					}),
				);
				console.log("[VideoClient] Sent video answer");
				this.startStats();
			} catch (err) {
				console.error("[VideoClient] Error handling video offer:", err);
			}
		}
		if (msg.type === "video_ice" && this.pc) {
			await this.pc.addIceCandidate(msg.data as RTCIceCandidateInit);
		}
	}

	private startControlPolling() {
		if (this.controlPollTimer !== null) {
			return;
		}
		this.controlPollTimer = window.setInterval(() => {
			this.sendControlCheck();
		}, 1000);
	}

	private stopControlPolling() {
		if (this.controlPollTimer === null) {
			return;
		}
		window.clearInterval(this.controlPollTimer);
		this.controlPollTimer = null;
	}

	private sendControlCheck() {
		if (this.ws?.readyState !== WebSocket.OPEN) {
			return;
		}
		this.ws.send(
			JSON.stringify({
				type: "control_check",
				client_id: this.clientId,
				data: {},
			}),
		);
	}

	public closePeerConnection() {
		this.clearPeerDisconnectTimer();
		if (this.statsTimer !== null) {
			window.clearInterval(this.statsTimer);
			this.statsTimer = null;
		}
		if (this.pc) {
			for (const receiver of this.pc.getReceivers()) {
				receiver.track?.stop();
			}
			this.pc.close();
			this.pc = null;
		}
		this.previousInboundStats.clear();
	}

	private clearPeerDisconnectTimer() {
		if (this.peerDisconnectTimer === null) return;
		window.clearTimeout(this.peerDisconnectTimer);
		this.peerDisconnectTimer = null;
	}

	private restartPeerConnection(reason: string) {
		console.warn(`[VideoClient] Restarting peer connection: ${reason}`);
		this.closePeerConnection();
		if (this.ws?.readyState === WebSocket.OPEN) {
			this.waitingForOffer = false;
			this.sendControlCheck();
			this.startControlPolling();
		}
	}

	private scheduleReconnect() {
		if (this.disposed || this.reconnectTimer !== null) {
			return;
		}

		const delayMs = Math.min(3000 * 2 ** this.reconnectAttempt, 15000);
		this.reconnectAttempt += 1;

		this.reconnectTimer = window.setTimeout(() => {
			this.reconnectTimer = null;
			if (!this.disposed) {
				this.connectWebSocket();
			}
		}, delayMs);
	}

	private clearReconnectTimer() {
		if (this.reconnectTimer === null) {
			return;
		}
		window.clearTimeout(this.reconnectTimer);
		this.reconnectTimer = null;
	}

	public dispose() {
		if (this.disposed) return;
		this.disposed = true;
		this.waitingForOffer = false;
		this.clearReconnectTimer();
		this.stopControlPolling();
		this.closePeerConnection();

		const ws = this.ws;
		this.ws = null;
		if (ws) {
			ws.onopen = null;
			ws.onmessage = null;
			ws.onerror = null;
			ws.onclose = null;
			if (
				ws.readyState === WebSocket.OPEN ||
				ws.readyState === WebSocket.CONNECTING
			) {
				ws.close();
			}
		}
	}

	private startStats() {
		if (!this.pc || this.statsTimer) return;
		this.statsTimer = window.setInterval(async () => {
			if (!this.pc) return;
			const report = await this.pc.getStats();
			const streams: VideoStats["streams"] = [];
			report.forEach((stat) => {
				if (stat.type === "inbound-rtp" && stat.kind === "video") {
					const previous = this.previousInboundStats.get(stat.id);
					const elapsedMs = previous ? stat.timestamp - previous.timestamp : 0;
					const receivedBytes = previous
						? stat.bytesReceived - previous.bytesReceived
						: 0;
					const bitrateKbps =
						elapsedMs > 0 ? Math.round((receivedBytes * 8) / elapsedMs) : 0;
					this.previousInboundStats.set(stat.id, {
						bytesReceived: stat.bytesReceived || 0,
						timestamp: stat.timestamp,
					});
					const extended = stat as RTCInboundRtpStreamStats & {
						jitterBufferDelay?: number;
						jitterBufferEmittedCount?: number;
						totalDecodeTime?: number;
						framesDecoded?: number;
						framesDropped?: number;
					};
					streams.push({
						id: stat.trackIdentifier || stat.ssrc?.toString() || "video",
						fps: stat.framesPerSecond || 0,
						bitrateKbps,
						width: stat.frameWidth || 0,
						height: stat.frameHeight || 0,
						packetsLost: stat.packetsLost || 0,
						jitter: stat.jitter || 0,
						jitterBufferMs: extended.jitterBufferEmittedCount
							? Math.round(
									(extended.jitterBufferDelay || 0) * 1000 /
										extended.jitterBufferEmittedCount,
								)
							: 0,
						decodeMs: extended.framesDecoded
							? Math.round(
									(extended.totalDecodeTime || 0) * 1000 /
										extended.framesDecoded,
								)
							: 0,
						framesDropped: extended.framesDropped || 0,
					});
				}
			});
			this.onStats({ state: this.pc.connectionState, streams });
		}, 1000);
	}
}
