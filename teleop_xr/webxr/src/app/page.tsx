"use client";

import { Glasses, Moon, ScanEye, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useState } from "react";
import { RobotSettingsPanel } from "@/components/dashboard/RobotSettingsPanel";
import { TeleopPanel } from "@/components/dashboard/TeleopPanel";
import { Button } from "@/components/ui/button";

const XRScene = dynamic(
	() => import("@/components/xr/XRScene").then((mod) => mod.XRScene),
	{ ssr: false },
);

export type XRBackgroundMode = "passthrough" | "black";

export default function Home() {
	const [xrError, setXrError] = useState<string | null>(null);
	const [backgroundMode, setBackgroundMode] =
		useState<XRBackgroundMode>("passthrough");
	const [activeMode, setActiveMode] = useState<XRBackgroundMode | null>(null);

	const handleEnterXR = useCallback(() => {
		setXrError(null);
		setActiveMode(backgroundMode);
	}, [backgroundMode]);

	const handleExit = useCallback(() => {
		setActiveMode(null);
	}, []);

	return (
		<main className="min-h-screen bg-transparent p-8">
			{activeMode && (
				<XRScene
					backgroundMode={activeMode}
					onError={(message) => setXrError(message)}
					onExit={handleExit}
				/>
			)}
			<div className="relative z-10 mx-auto max-w-5xl space-y-8">
				<header className="flex items-center justify-between rounded-xl border bg-background/60 px-6 py-5 backdrop-blur">
					<div>
						<h1 className="text-3xl font-bold tracking-tight">
							OpenArm 遥操作
						</h1>
						<p className="text-muted-foreground">Pico MR 双臂控制界面</p>
					</div>
					<div className="flex items-center gap-3">
						{activeMode === null ? (
							<>
								<div
									className="flex items-center gap-1 rounded-lg border bg-background/80 p-1"
									aria-label="WebXR 背景模式"
								>
									<Button
										type="button"
										size="sm"
										variant={backgroundMode === "passthrough" ? "default" : "ghost"}
										className="gap-2"
										onClick={() => setBackgroundMode("passthrough")}
										aria-pressed={backgroundMode === "passthrough"}
									>
										<ScanEye className="h-4 w-4" />
										半透视背景
									</Button>
									<Button
										type="button"
										size="sm"
										variant={backgroundMode === "black" ? "default" : "ghost"}
										className="gap-2"
										onClick={() => setBackgroundMode("black")}
										aria-pressed={backgroundMode === "black"}
									>
										<Moon className="h-4 w-4" />
										纯黑背景
									</Button>
								</div>
								<Button
									size="lg"
									className="gap-2"
									onClick={handleEnterXR}
									variant="default"
								>
									<Glasses className="h-4 w-4" />
									进入 WebXR
								</Button>
							</>
						) : (
							<Button
								size="lg"
								className="gap-2"
								onClick={handleExit}
								variant="destructive"
							>
								<X className="h-4 w-4" />
								Exit XR
							</Button>
						)}
					</div>
				</header>

				{xrError ? (
					<div className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm">
						<span className="font-medium text-destructive">XR:</span> {xrError}
					</div>
				) : null}

				<div className="grid items-start gap-6 md:grid-cols-2">
					<TeleopPanel />
					<RobotSettingsPanel />
				</div>
			</div>
		</main>
	);
}
