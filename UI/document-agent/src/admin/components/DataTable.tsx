import { Button, CircularProgress } from "@mui/material";
import { Fragment, type ReactNode } from "react";
import "./components.css";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  className?: string;
}

export interface Pagination {
  offset: number;
  limit: number;
  /** Total rows, when the API gives it */
  total?: number;
  /** Without a total: whether there is a next page */
  hasMore?: boolean;
  /** Both buttons off (DataTable sets it while a page loads) */
  disabled?: boolean;
  onChange: (offset: number) => void;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  /** null: first load in progress (or failed, if `error` is set) */
  rows: T[] | null;
  rowKey: (row: T) => string;
  /** A reload is in progress: the current rows are dimmed */
  loading?: boolean;
  /** The last load failed: shown above the (dimmed) previous rows, if any */
  error?: string | null;
  /** Shows a "Reintentar" button next to the error */
  onRetry?: () => void;
  emptyText: string;
  rowClassName?: (row: T) => string | undefined;
  /** Content shown in a full-width row under `row` (e.g. its details); null/undefined shows nothing */
  expandedRow?: (row: T) => ReactNode;
  pagination?: Pagination;
  /** For screen readers */
  caption?: string;
}

const PaginationBar = ({ offset, limit, total, hasMore, disabled = false, onChange, shown }: Pagination & { shown: number }) => {
  const from = shown ? offset + 1 : 0;
  const to = offset + shown;
  const hasNext = total !== undefined ? to < total : !!hasMore;
  return (
    <div className="admin-pagination">
      <span className="admin-muted">
        {shown === 0
          ? total !== undefined
            ? `0 de ${total}`
            : "Sin resultados"
          : total !== undefined
            ? `Mostrando ${from}–${to} de ${total}`
            : `Mostrando ${from}–${to}`}
      </span>
      <Button size="small" disabled={disabled || offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Anterior
      </Button>
      <Button size="small" disabled={disabled || !hasNext} onClick={() => onChange(offset + limit)}>
        Siguiente
      </Button>
    </div>
  );
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  error = null,
  onRetry,
  emptyText,
  rowClassName,
  expandedRow,
  pagination,
  caption,
}: DataTableProps<T>) {
  const dimmed = loading || !!error;
  return (
    <>
      {error && (
        <p className="admin-msg admin-msg--error" role="alert">
          {error}{" "}
          {onRetry && (
            <Button size="small" onClick={onRetry}>
              Reintentar
            </Button>
          )}
        </p>
      )}
      {rows === null ? (
        !error && (
          <div className="admin-center">
            <CircularProgress size={24} />
          </div>
        )
      ) : (
        <>
          <div
            className={`admin-table-wrap admin-table-wrap--full${dimmed ? " admin-table-wrap--loading" : ""}`}
            aria-busy={loading}
          >
            <table className="admin-table">
              {caption && <caption className="admin-visually-hidden">{caption}</caption>}
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column.key} scope="col" className={column.className}>
                      {column.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={columns.length} className="admin-table-empty">
                      {emptyText}
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => {
                    const extra = expandedRow?.(row);
                    return (
                      <Fragment key={rowKey(row)}>
                        <tr className={rowClassName?.(row)}>
                          {columns.map((column) => (
                            <td key={column.key} className={column.className}>
                              {column.render(row)}
                            </td>
                          ))}
                        </tr>
                        {extra != null && (
                          <tr className="admin-row-expanded">
                            <td colSpan={columns.length}>{extra}</td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          {pagination && <PaginationBar {...pagination} disabled={pagination.disabled || loading} shown={rows.length} />}
        </>
      )}
    </>
  );
}
