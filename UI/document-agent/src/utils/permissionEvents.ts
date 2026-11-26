// Raised when the API refuses a call (403): what the UI shows may be out of date
const STALE = "permissions:stale";

export const notifyPermissionsStale = () => window.dispatchEvent(new Event(STALE));

export const onPermissionsStale = (listener: () => void) => {
  window.addEventListener(STALE, listener);
  return () => window.removeEventListener(STALE, listener);
};
