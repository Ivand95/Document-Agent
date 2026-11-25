import { bearer, http } from "../axios";

export interface FolderSetting {
  name: string;
  enabled: boolean;
  department: string | null;
  found: boolean;
}

export interface IngestionWindow {
  start: string;
  end: string;
  tz: string;
  open_now: boolean;
}

export interface IngestionConfig {
  audio_root: string;
  configured: boolean;
  folders: FolderSetting[];
  departments: { code: string; label: string }[];
  window: IngestionWindow;
}

export interface TargetReport {
  remote: number;
  indexed: number;
  pending: number;
  orphans: number;
  pending_files: string[];
  folders?: Record<string, { remote: number; indexed: number; pending: number; orphans: number }>;
}

export interface IngestionReport {
  report: {
    generated_at: string;
    error?: string;
    docs?: TargetReport;
    audio?: TargetReport;
  } | null;
  status: { running: boolean; mode: string; started_at: string } | null;
  last_run: {
    mode: string;
    started_at: string;
    finished_at: string;
    results: Record<string, unknown>;
  } | null;
  run_request: { mode: string } | null;
  report_requested: boolean;
  window: IngestionWindow;
}

export const getIngestionConfig = (token: string) =>
  http.get<IngestionConfig>("/admin/ingestion/config", bearer(token));

export const saveIngestionFolders = (
  token: string,
  folders: Pick<FolderSetting, "name" | "enabled" | "department">[],
) =>
  http.post<{ saved: number; relabeled: string[] }>(
    "/admin/ingestion/folders",
    { folders },
    bearer(token),
  );

export const getIngestionReport = (token: string) =>
  http.get<IngestionReport>("/admin/ingestion/report", bearer(token));

export const refreshIngestionReport = (token: string) =>
  http.post("/admin/ingestion/report/refresh", null, bearer(token));

export const requestIngestionRun = (token: string, mode: "now" | "night") =>
  http.post("/admin/ingestion/run", { mode }, bearer(token));
