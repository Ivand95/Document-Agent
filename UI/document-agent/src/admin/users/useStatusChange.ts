import { useState, type ComponentProps } from "react";
import { setUserStatus, type UserAction, type UserRow, type UserStatus } from "../../utils/api/users";
import { apiErrorMessage } from "../../utils/apiError";
import type { ConfirmDialog } from "../components/ConfirmDialog";
import { ACTION_STATUS, displayName } from "./userLabels";

type Target = Pick<UserRow, "id" | "name" | "email">;

/** Changes a user's status. Disabling asks for confirmation first: render
 * <ConfirmDialog {...statusChange.confirmProps} /> next to the buttons. */
export const useStatusChange = (token: string, onChanged: (id: string, status: UserStatus) => void) => {
  const [confirming, setConfirming] = useState<Target | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (user: Target, action: UserAction) => {
    setBusyId(user.id);
    setError(null);
    try {
      const { data } = await setUserStatus(token, user.id, ACTION_STATUS[action]);
      onChanged(user.id, data.status);
    } catch (e) {
      setError(apiErrorMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  const request = (user: Target, action: UserAction) => {
    if (action === "disable") setConfirming(user);
    else void run(user, action);
  };

  const confirmProps: ComponentProps<typeof ConfirmDialog> = {
    open: confirming !== null,
    title: "¿Deshabilitar usuario?",
    message: confirming ? `${displayName(confirming)} perderá el acceso en menos de 30 segundos.` : "",
    confirmLabel: "Deshabilitar",
    destructive: true,
    busy: busyId !== null,
    onCancel: () => setConfirming(null),
    onConfirm: () => {
      if (confirming) void run(confirming, "disable").then(() => setConfirming(null));
    },
  };

  return { request, busyId, error, confirmProps };
};
