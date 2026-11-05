/** Unknown reservation totals remain unknown, rather than a measured zero. */
export function availableBudget(spent: number, reserved: number | null, cap: number): number | null {
  if (!Number.isSafeInteger(spent) || spent < 0 || !Number.isSafeInteger(cap) || cap < 0 ||
      (reserved !== null && (!Number.isSafeInteger(reserved) || reserved < 0))) {
    throw new RangeError("Budget values must be nonnegative safe integers");
  }
  if (reserved === null) return 0;
  return Math.max(0, cap - spent - reserved);
}
