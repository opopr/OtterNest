import { useEffect, useState } from "react";

export function useListPage<T>(rows: readonly T[], resetKey: string, initialSize = 20) {
  const [index, setIndex] = useState(0);
  const [size, setSize] = useState(initialSize);
  useEffect(() => { setIndex(0); }, [resetKey, size]);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const page = Math.min(index, pages - 1);
  useEffect(() => { setIndex(i => Math.min(i, pages - 1)); }, [pages]);
  return { rows: rows.slice(page * size, (page + 1) * size), total: rows.length, page, pages, size, setIndex, setSize };
}

type PaginationProps = Omit<ReturnType<typeof useListPage<unknown>>, "rows"> & { label: string };
export default function ListPagination({total, page, pages, size, setIndex, setSize, label}: PaginationProps) {
  if (!total) return null;
  return <div className="task-pagination list-pagination" role="group" aria-label={label}>
    <span>第 {page * size + 1}–{Math.min(total, (page + 1) * size)} 条，共 {total} 条</span>
    <div><label>每页 <select aria-label={label + "每页条数"} value={size} onChange={event => setSize(Number(event.target.value))}>
      {[10,20,50].map(n => <option key={n} value={n}>{n}</option>)}
    </select></label><button type="button" className="secondary-button compact" disabled={page === 0} onClick={() => setIndex(page - 1)}>上一页</button>
    <span aria-live="polite">{page + 1}/{pages}</span><button type="button" className="secondary-button compact" disabled={page + 1 >= pages} onClick={() => setIndex(page + 1)}>下一页</button></div>
  </div>;
}
