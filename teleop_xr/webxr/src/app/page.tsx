import { PicoUltraStereoTest } from "@/components/xr/PicoUltraStereoTest";

export type XRBackgroundMode = "passthrough" | "black";

export default function Home() {
	return <PicoUltraStereoTest transport="rtc" />;
}
