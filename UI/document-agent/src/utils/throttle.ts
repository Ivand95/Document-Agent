/** Returns a gate that lets a call through at most once every `minMs` (time passed in, for testing). */
export const createThrottle = (minMs: number) => {
  let last = Number.NEGATIVE_INFINITY;
  return (now: number) => {
    if (now - last < minMs) return false;
    last = now;
    return true;
  };
};
