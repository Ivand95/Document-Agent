import { Autocomplete } from "@mui/material";
import { useEffect, useState } from "react";
import { listUsers, type UserRow } from "../../utils/api/users";
import { adminContainer } from "../components/adminContainer";
import { displayName } from "../users/userLabels";

const SEARCH_DELAY_MS = 300;
const MAX_OPTIONS = 10;

/** Picks a user by name or email (GET /admin/users?q=, 300 ms after typing stops). Needs the Usuarios area. */
export const UserPicker = ({ token, onPick }: { token: string; onPick: (user: UserRow) => void }) => {
  const [input, setInput] = useState("");
  const [result, setResult] = useState<{ q: string; users: UserRow[] } | null>(null);
  const q = input.trim();

  useEffect(() => {
    if (!q) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      listUsers(token, { q, limit: MAX_OPTIONS }, controller.signal)
        .then(({ data }) => {
          if (!controller.signal.aborted) setResult({ q, users: data.users });
        })
        .catch(() => {
          if (!controller.signal.aborted) setResult({ q, users: [] });
        });
    }, SEARCH_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [token, q]);

  const options = q && result?.q === q ? result.users : [];
  const loading = !!q && result?.q !== q;

  return (
    <Autocomplete<UserRow, false, false, false>
      className="admin-user-picker"
      options={options}
      value={null}
      inputValue={input}
      onInputChange={(_, value, reason) => {
        if (reason !== "reset") setInput(value);
      }}
      onChange={(_, user) => {
        if (!user) return;
        onPick(user);
        setInput("");
      }}
      getOptionLabel={(user) => displayName(user)}
      getOptionKey={(user) => user.id}
      isOptionEqualToValue={(a, b) => a.id === b.id}
      filterOptions={(all) => all}
      renderOption={(props, user) => {
        const { key, ...rest } = props;
        return (
          <li key={key} {...rest}>
            <div className="admin-audit-cell">
              <span>{displayName(user)}</span>
              <small>{user.email ?? "—"}</small>
            </div>
          </li>
        );
      }}
      loading={loading}
      loadingText="Buscando…"
      noOptionsText={q ? "Sin resultados" : "Escribe un nombre o email"}
      slotProps={{ popper: { container: adminContainer }, paper: { className: "admin-menu" } }}
      renderInput={(params) => (
        <div ref={params.InputProps.ref}>
          <input
            {...params.inputProps}
            type="search"
            className="admin-input"
            placeholder="Usuario que actuó"
            aria-label="Filtrar por usuario que actuó"
          />
        </div>
      )}
    />
  );
};
