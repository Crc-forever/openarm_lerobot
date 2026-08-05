export type YDragInput = {
	yPressed: boolean;
	previousYPressed: boolean;
	bPressed: boolean;
	teleopEngaged: boolean;
	hasTarget: boolean;
};

export const shouldBeginYDrag = ({
	yPressed,
	previousYPressed,
	bPressed,
	teleopEngaged,
	hasTarget,
}: YDragInput): boolean =>
	yPressed &&
	!previousYPressed &&
	!bPressed &&
	!teleopEngaged &&
	hasTarget;

export const shouldEndYDrag = (
	yPressed: boolean,
	bPressed: boolean,
	teleopEngaged: boolean,
): boolean => !yPressed || bPressed || teleopEngaged;
