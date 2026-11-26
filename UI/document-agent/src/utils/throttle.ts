/** Runs `run` at most once every `minMs`. A call inside the window is not dropped: one run is
 * scheduled for the end of the window (extra calls meanwhile are merged into it). */
export const createTrailingThrottle = (
  minMs: number,
  run: () => void,
  clock: {
    now: () => number;
    setTimeout: (fn: () => void, ms: number) => unknown;
    clearTimeout: (id: unknown) => void;
  } = {
    now: () => Date.now(),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
  },
) => {
  let last = Number.NEGATIVE_INFINITY;
  let timer: unknown = null;

  const execute = () => {
    timer = null;
    last = clock.now();
    run();
  };

  return {
    call() {
      if (timer !== null) return;
      const wait = last + minMs - clock.now();
      if (wait <= 0) execute();
      else timer = clock.setTimeout(execute, wait);
    },
    /** Records "ran now" without running (the first load has just started). */
    markRan() {
      last = clock.now();
    },
    cancel() {
      if (timer !== null) clock.clearTimeout(timer);
      timer = null;
    },
  };
};
