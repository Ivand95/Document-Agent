import ArrowBack from "@mui/icons-material/ArrowBack";
import Refresh from "@mui/icons-material/Refresh";
import { Button, CircularProgress } from "@mui/material";
import { isAxiosError } from "axios";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { ThemeToggle } from "../components/ThemeToggle";
import { isAdminDepartment } from "../utils/admin";
import {
  getIngestionConfig,
  getIngestionReport,
  refreshIngestionReport,
  requestIngestionRun,
  saveIngestionFolders,
  type FolderSetting,
  type IngestionConfig,
  type IngestionReport,
  type TargetReport,
} from "../utils/axios";
import "./style.css";

const POLL_MS = 30_000;

const formatDate = (iso?: string) =>
  iso ? new Date(iso).toLocaleString("es-DO", { dateStyle: "short", timeStyle: "short" }) : "—";

const errorText = (error: unknown) =>
  isAxiosError(error)
    ? (error.response?.data?.detail as string | undefined) ?? error.message
    : "Error inesperado";

// ---- Folders ----

const FoldersSection = ({ token }: { token: string }) => {
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
      .catch((e) => setMessage({ ok: false, text: errorText(e) }));
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
      setMessage({ ok: false, text: errorText(e) });
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

// ---- Report ----

const Stat = ({ label, value, tone }: { label: string; value: number; tone?: "warn" }) => (
  <div className={`admin-stat${tone === "warn" && value > 0 ? " admin-stat--warn" : ""}`}>
    <span className="admin-stat-value">{value}</span>
    <span className="admin-stat-label">{label}</span>
  </div>
);

const TargetCard = ({ title, data }: { title: string; data?: TargetReport }) => {
  if (!data) return null;
  const folders = Object.entries(data.folders ?? {}).filter(([, v]) => v.pending > 0);
  return (
    <div className="admin-target">
      <h3>{title}</h3>
      <div className="admin-stats">
        <Stat label="En SharePoint" value={data.remote} />
        <Stat label="Cargados" value={data.indexed} />
        <Stat label="Pendientes" value={data.pending} tone="warn" />
      </div>
      {folders.length > 0 && (
        <p className="admin-muted">
          Pendientes por extensión: {folders.map(([name, v]) => `${name} (${v.pending})`).join(", ")}
        </p>
      )}
      {data.pending_files.length > 0 && (
        <details>
          <summary>Ver archivos pendientes ({Math.min(data.pending, data.pending_files.length)} de {data.pending})</summary>
          <ul className="admin-files">
            {data.pending_files.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
};

const ReportSection = ({ token }: { token: string }) => {
  const [data, setData] = useState<IngestionReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setData((await getIngestionReport(token)).data);
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    }
  }, [token]);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (action: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await action();
      setMessage({ ok: true, text: ok });
      await load();
    } catch (e) {
      setMessage({ ok: false, text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  const totalPending = useMemo(
    () => (data?.report?.docs?.pending ?? 0) + (data?.report?.audio?.pending ?? 0),
    [data],
  );

  if (!data) return <section className="admin-card"><CircularProgress size={22} /></section>;

  const { report, status, last_run: lastRun, run_request: request, window } = data;
  const running = !!status?.running;

  return (
    <section className="admin-card">
      <div className="admin-card-head">
        <h2>Reporte de carga</h2>
        <Button
          size="small"
          startIcon={<Refresh />}
          disabled={busy || data.report_requested}
          onClick={() => act(() => refreshIngestionReport(token), "Reporte solicitado. Puede tardar un minuto.")}
        >
          {data.report_requested ? "Actualizando…" : "Actualizar reporte"}
        </Button>
      </div>

      {!report ? (
        <p className="admin-muted">Aún no hay reporte. El indexador lo genera al arrancar y cada cierto tiempo.</p>
      ) : report.error ? (
        <p className="admin-msg admin-msg--error">No se pudo generar el reporte: {report.error}</p>
      ) : (
        <>
          <p className={`admin-summary${totalPending ? " admin-summary--warn" : ""}`}>
            {totalPending
              ? `Hay ${totalPending} archivo(s) pendientes de cargar.`
              : "Todo está al día: no hay archivos pendientes."}
          </p>
          <div className="admin-targets">
            <TargetCard title="Documentos" data={report.docs} />
            <TargetCard title="Audios" data={report.audio} />
          </div>
        </>
      )}
      <p className="admin-muted">Reporte generado: {formatDate(report?.generated_at)}</p>

      <h3>¿Cuándo cargar los pendientes?</h3>
      <p className="admin-muted">
        Por defecto se cargan en el horario nocturno ({window.start}–{window.end}, {window.tz}) para no cargar el
        servidor{window.open_now ? "; ahora mismo el horario está abierto" : ""}.
      </p>
      <div className="admin-actions">
        <Button
          variant="contained"
          disabled={busy || running || request?.mode === "now"}
          onClick={() => {
            if (window.open_now || confirm("Cargar ahora puede hacer más lento el sistema para los usuarios. ¿Continuar?")) {
              act(() => requestIngestionRun(token, "now"), "Carga solicitada: empieza en cuanto el indexador la reciba (menos de 1 minuto).");
            }
          }}
        >
          Cargar ahora
        </Button>
        <Button
          variant="outlined"
          disabled={busy || running || request?.mode !== "now"}
          onClick={() => act(() => requestIngestionRun(token, "night"), "Solicitud cancelada: se cargará en el horario nocturno.")}
        >
          Dejar para el horario nocturno
        </Button>
      </div>
      {running && (
        <p className="admin-msg admin-msg--warn">
          Carga en curso ({status?.mode === "now" ? "manual" : "nocturna"}) desde {formatDate(status?.started_at)}.
        </p>
      )}
      {!running && request?.mode === "now" && (
        <p className="admin-msg admin-msg--warn">Carga manual solicitada, esperando al indexador…</p>
      )}
      {lastRun && (
        <p className="admin-muted">
          Última carga: {formatDate(lastRun.finished_at)} ({lastRun.mode === "now" ? "manual" : "nocturna"}).
        </p>
      )}
      {message && <p className={`admin-msg ${message.ok ? "admin-msg--ok" : "admin-msg--error"}`}>{message.text}</p>}
    </section>
  );
};

// ---- Page ----

export const Admin = ({ userInfo }: { userInfo: UserInfo }) => {
  const navigate = useNavigate();

  return (
    <div className="admin-page">
      <header className="admin-header">
        <Button startIcon={<ArrowBack />} onClick={() => navigate("/chat")} sx={{ color: "var(--chat-text)" }}>
          Volver al chat
        </Button>
        <h1>Configuración de ingesta</h1>
        <ThemeToggle />
      </header>
      <main className="admin-main">
        {isAdminDepartment(userInfo.department) ? (
          <>
            <FoldersSection token={userInfo.access_token} />
            <ReportSection token={userInfo.access_token} />
          </>
        ) : (
          <p className="admin-msg admin-msg--error">Solo disponible para el departamento de Tecnología.</p>
        )}
      </main>
    </div>
  );
};
