"use client";

import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { useAppStore } from "@/lib/store";

export function TeleopPanel() {
	const teleopLifecycle = useAppStore((state) => state.teleopLifecycle);
	const teleopEngaged = useAppStore((state) => state.teleopEngaged);

	const lifecycleLabel: Record<typeof teleopLifecycle, string> = {
		disconnected: "等待进入 MR",
		connecting: "正在连接服务器",
		connected: "服务器已连接",
		loading_robot: "正在加载机器人骨架",
		ready: "遥操作已就绪",
		reconnecting: "连接中断，正在重连",
		error: "连接异常",
	};

	const lifecycleColorClass: Record<typeof teleopLifecycle, string> = {
		disconnected: "text-red-600",
		connecting: "text-amber-600",
		connected: "text-blue-600",
		loading_robot: "text-orange-600",
		ready: "text-green-600",
		reconnecting: "text-amber-600",
		error: "text-red-700",
	};

	return (
		<Card className="w-full">
			<CardHeader>
				<CardTitle>遥操作状态</CardTitle>
				<CardDescription>此状态仅表示当前设备的 MR 控制连接</CardDescription>
			</CardHeader>
			<CardContent className="space-y-6">
				<div className="flex items-center justify-between rounded-lg border px-3 py-2">
					<Label>系统状态</Label>
					<span
						className={`text-sm font-medium ${lifecycleColorClass[teleopLifecycle]}`}
					>
						{lifecycleLabel[teleopLifecycle]}
					</span>
				</div>

				<div className="flex items-center justify-between rounded-lg border px-3 py-2">
					<Label>控制状态</Label>
					<span
						className={`text-sm font-medium ${
							teleopEngaged ? "text-green-600" : "text-muted-foreground"
						}`}
					>
						{teleopEngaged ? "双侧握已按下，正在控制" : "待机"}
					</span>
				</div>

				<div className="space-y-3 rounded-lg bg-muted/50 p-4 text-sm">
					<p className="font-medium">手柄操作</p>
					<ul className="list-disc space-y-2 pl-5 text-muted-foreground">
						<li>同时按住左右侧握键：接管机械臂</li>
						<li>松开任意侧握键：停止更新并保持当前位置</li>
						<li>左右食指扳机：连续控制对应夹爪</li>
						<li>松开侧握，按住 Y + B 1.5 秒：重置操作坐标轴</li>
						<li>双击侧握键：重置机械臂关节姿态</li>
					</ul>
				</div>
			</CardContent>
		</Card>
	);
}
