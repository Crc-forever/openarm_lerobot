"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useAppStore } from "@/lib/store";

export function RobotSettingsPanel() {
	const [isOpen, setIsOpen] = useState(false);

	// Access store state and actions
	const robotSettings = useAppStore((state) => state.robotSettings);
	const setRobotSettings = useAppStore((state) => state.setRobotSettings);

	// Settings values from store
	const { robotVisible, showAxes, spawnDistance, spawnHeight } = robotSettings;

	const handleRobotVisibilityChange = (checked: boolean) => {
		setRobotSettings({ robotVisible: checked });
	};

	const handleShowAxesChange = (checked: boolean) => {
		setRobotSettings({ showAxes: checked });
	};

	const handleSpawnDistanceChange = (value: number[]) => {
		setRobotSettings({ spawnDistance: value[0] });
	};

	const handleSpawnHeightChange = (value: number[]) => {
		setRobotSettings({ spawnHeight: value[0] });
	};

	return (
		<Card className="w-full">
			<CardHeader
				className="cursor-pointer select-none"
				onClick={() => setIsOpen(!isOpen)}
			>
				<div className="flex items-center justify-between">
					<div>
						<CardTitle>骨架显示设置</CardTitle>
						<CardDescription>调整 Pico 中机器人骨架的显示</CardDescription>
					</div>
					{isOpen ? (
						<ChevronUp className="h-4 w-4" />
					) : (
						<ChevronDown className="h-4 w-4" />
					)}
				</div>
			</CardHeader>
			{isOpen && (
				<CardContent className="space-y-6">
					{/* Visualization Section */}
					<div className="space-y-4">
						<div className="flex items-center justify-between space-x-2">
							<Label htmlFor="robot-visible">显示机器人骨架</Label>
							<Switch
								id="robot-visible"
								checked={robotVisible}
								onCheckedChange={handleRobotVisibilityChange}
							/>
						</div>

						<div className="flex items-center justify-between space-x-2">
							<Label htmlFor="show-axes">显示坐标轴</Label>
							<Switch
								id="show-axes"
								checked={showAxes}
								onCheckedChange={handleShowAxesChange}
							/>
						</div>

						<div className="space-y-2">
							<div className="flex items-center justify-between">
								<Label htmlFor="spawn-distance">骨架显示距离</Label>
								<span className="text-sm text-muted-foreground">
									{spawnDistance.toFixed(1)}m
								</span>
							</div>
							<Slider
								id="spawn-distance"
								min={0.5}
								max={3.0}
								step={0.1}
								value={[spawnDistance]}
								onValueChange={handleSpawnDistanceChange}
							/>
						</div>

						<div className="space-y-2">
							<div className="flex items-center justify-between">
								<Label htmlFor="spawn-height">骨架高度偏移</Label>
								<span className="text-sm text-muted-foreground">
									{spawnHeight.toFixed(1)}m
								</span>
							</div>
							<Slider
								id="spawn-height"
								min={-1.0}
								max={1.0}
								step={0.1}
								value={[spawnHeight]}
								onValueChange={handleSpawnHeightChange}
							/>
						</div>
					</div>

					<p className="text-xs text-muted-foreground">
						距离和高度会在下一次 Y + B 重置坐标轴时生效。
					</p>
				</CardContent>
			)}
		</Card>
	);
}
