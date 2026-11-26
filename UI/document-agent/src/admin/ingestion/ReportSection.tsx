import Refresh from "@mui/icons-material/Refresh";
import { Button, CircularProgress } from "@mui/material";
import { useCallback, useEffect, useMemo, useState } from "react";
import { apiErrorMessage } from "../../utils/apiError";
import {
  getIngestionReport,
  refreshIngestionReport,
  requestIngestionRun,
  type IngestionReport,
  type TargetReport,
} from "../../utils/api/ingestion";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { formatDateTime } from "../format";

const POLL_MS = 30_000;

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

export const ReportSection = ({ token }: { token: string }) => {
  const [data, setData] = useState<IngestionReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmNow, setConfirmNow] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setData((await getIngestionReport(token)).data);
    } catch (e) {
      setMessage({ ok: false, text: apiErrorMessage(e) });
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
      setMessage({ ok: false, text: apiErrorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  const totalPending = useMemo(
    () => (data?.report?.docs?.pending ?? 0) + (data?.report?.audio?.pending ?? 0),
    [data],
  );

  if (!data) {
    return (
      <section className="admin-card">
        {message ? <p className="admin-msg admin-msg--error">{message.text}</p> : <CircularProgress size={22} />}
      </section>
    );
  }

  const { report, status, last_run: lastRun, run_request: request, window: win } = data;
  const running = !!status?.running;
  const runNow = () =>
    act(
      () => requestIngestionRun(token, "now"),
      "Carga solicitada: empieza en cuanto el indexador la reciba (menos de 1 minuto).",
    );

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
      <p className="admin-muted">Reporte generado: {formatDateTime(report?.generated_at)}</p>

      <h3>¿Cuándo cargar los pendientes?</h3>
      <p className="admin-muted">
        Por defecto se cargan en el horario nocturno ({win.start}–{win.end}, {win.tz}) para no cargar el
        servidor{win.open_now ? "; ahora mismo el horario está abierto" : ""}.
      </p>
      <div className="admin-actions">
        <Button
          variant="contained"
          disabled={busy || running || request?.mode === "now"}
          onClick={() => (win.open_now ? runNow() : setConfirmNow(true))}
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
          Carga en curso ({status?.mode === "now" ? "manual" : "nocturna"}) desde {formatDateTime(status?.started_at)}.
        </p>
      )}
      {!running && request?.mode === "now" && (
        <p className="admin-msg admin-msg--warn">Carga manual solicitada, esperando al indexador…</p>
      )}
      {lastRun && (
        <p className="admin-muted">
          Última carga: {formatDateTime(lastRun.finished_at)} ({lastRun.mode === "now" ? "manual" : "nocturna"}).
        </p>
      )}
      {message && <p className={`admin-msg ${message.ok ? "admin-msg--ok" : "admin-msg--error"}`}>{message.text}</p>}

      <ConfirmDialog
        open={confirmNow}
        title="¿Cargar ahora?"
        message="Fuera del horario nocturno, cargar ahora puede hacer más lento el sistema para los usuarios."
        confirmLabel="Cargar ahora"
        busy={busy}
        onCancel={() => setConfirmNow(false)}
        onConfirm={() => {
          setConfirmNow(false);
          runNow();
        }}
      />
    </section>
  );
};
