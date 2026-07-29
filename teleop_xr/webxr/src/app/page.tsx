"use client";

import { Glasses, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useState } from "react";
import { RobotSettingsPanel } from "@/components/dashboard/RobotSettingsPanel";
import { TeleopPanel } from "@/components/dashboard/TeleopPanel";
import { Button } from "@/components/ui/button";

const XRScene = dynamic(
	() => import("@/components/xr/XRScene").then((mod) => mod.XRScene),
	{ ssr: false },
);

export type XRMode = "passthrough" | null;

export default function Home() {
	const [xrError, setXrError] = useState<string | null>(null);
	const [selectedMode, setSelectedMode] = useState<XRMode>(null);

	const handleEnterPassthrough = useCallback(() => {
		setXrError(null);
		setSelectedMode("passthrough");
	}, []);

	const handleExit = useCallback(() => {
		setSelectedMode(null);
	}, []);

	return (
		<main className="min-h-screen bg-transparent p-8">
			{selectedMode && (
				<XRScene
					mode={selectedMode}
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
						{selectedMode === null ? (
							<Button
								size="lg"
								className="gap-2"
								onClick={handleEnterPassthrough}
								variant="default"
							>
								<Glasses className="h-4 w-4" />
								MR 透视遥操作
							</Button>
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
