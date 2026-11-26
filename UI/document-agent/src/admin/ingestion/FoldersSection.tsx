import { Button, CircularProgress } from "@mui/material";
import { useEffect, useState } from "react";
import { apiErrorMessage } from "../../utils/apiError";
import {
  getIngestionConfig,
  saveIngestionFolders,
  type FolderSetting,
  type IngestionConfig,
} from "../../utils/api/ingestion";

export const FoldersSection = ({ token }: { token: string }) => {
  const [config, setConfig] = useState<IngestionConfig | null>(null);
  const [folders, setFolders] = useState<FolderSetting[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getIngestionConfig(token)
      .then(({ data }) => {
        setConfig(data);
        setFolders(data.folders);
      })
      .catch((e) => setMessage({ ok: false, text: apiErrorMessage(e) }));
  }, [token]);

  const update = (name: string, patch: Partial<FolderSetting>) => {
    setFolders((prev) => prev.map((f) => (f.name === name ? { ...f, ...patch } : f)));
    setDirty(true);
    setMessage(null);
  };

  const setAll = (enabled: boolean) => {
    setFolders((prev) => prev.map((f) => (f.found ? { ...f, enabled } : f)));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await saveIngestionFolders(
        token,
        folders.map(({ name, enabled, department }) => ({ name, enabled, department })),
      );
      setDirty(false);
      setMessage({
        ok: true,
        text: data.relabeled.length
          ? `Guardado. Departamento actualizado en las llamadas de ${data.relabeled.length} carpeta(s).`
          : "Guardado.",
      });
    } catch (e) {
      setMessage({ ok: false, text: apiErrorMessage(e) });
    } finally {
      setSaving(false);
    }
  };

  const enabledCount = folders.filter((f) => f.enabled).length;

  return (
    <section className="admin-card">
      <h2>Carpetas de audio</h2>
      {!config ? (
        message ? <p className="admin-msg admin-msg--error">{message.text}</p> : <CircularProgress size={22} />
      ) : (
        <>
          <p className="admin-muted">
            Carpeta raíz (definida en <code>.env</code>): <strong>{config.audio_root || "(toda la biblioteca)"}</strong>.
            Cada subcarpeta es una extensión. Elige cuáles se cargan y a qué departamento pertenecen: ese valor se
            guarda en la metadata de cada llamada.
          </p>
          {!config.configured && (
            <p className="admin-msg admin-msg--warn">
              Aún no hay selección guardada: mientras tanto se cargan <strong>todas</strong> las carpetas. Al guardar,
              solo se cargarán las marcadas.
            </p>
          )}
          {folders.length === 0 ? (
            <p className="admin-muted">
              Aún no se conocen las subcarpetas. El indexador las publica con el primer reporte; usa «Actualizar
              reporte» abajo.
            </p>
          ) : (
            <>
              <div className="admin-toolbar">
                <span className="admin-muted">
                  {enabledCount} de {folders.length} seleccionadas
                </span>
                <Button size="small" onClick={() => setAll(true)}>Marcar todas</Button>
                <Button size="small" onClick={() => setAll(false)}>Quitar todas</Button>
              </div>
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Cargar</th>
                      <th>Extensión</th>
                      <th>Departamento</th>
                    </tr>
                  </thead>
                  <tbody>
                    {folders.map((f) => (
                      <tr key={f.name}>
                        <td>
                          <input
                            type="checkbox"
                            checked={f.enabled}
                            aria-label={`Cargar carpeta ${f.name}`}
                            onChange={(e) => update(f.name, { enabled: e.target.checked })}
                          />
                        </td>
                        <td>
                          {f.name}
                          {!f.found && <span className="admin-badge" title="Ya no existe en SharePoint">no encontrada</span>}
                        </td>
                        <td>
                          <select
                            value={f.department ?? ""}
                            aria-label={`Departamento de ${f.name}`}
                            onChange={(e) => update(f.name, { department: e.target.value || null })}
                          >
                            <option value="">Sin clasificar</option>
                            {config.departments.map((d) => (
                              <option key={d.code} value={d.code}>{d.label}</option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <div className="admin-actions">
            <Button variant="contained" onClick={save} disabled={!dirty || saving}>
              {saving ? "Guardando…" : "Guardar selección"}
            </Button>
            {message && (
              <span className={`admin-msg ${message.ok ? "admin-msg--ok" : "admin-msg--error"}`}>{message.text}</span>
            )}
          </div>
        </>
      )}
    </section>
  );
};
