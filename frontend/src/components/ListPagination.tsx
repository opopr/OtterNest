import { useEffect, useLayoutEffect, useState } from "react";

export function useListPage<T>(rows: readonly T[], resetKey: string, initialSize = 20) {
  const [index, setIndex] = useState(0);
  const [size, setSize] = useState(initialSize);
  useLayoutEffect(() => { setIndex(0); }, [resetKey, size]);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const page = Math.min(index, pages - 1);
  useEffect(() => { setIndex(i => Math.min(i, pages - 1)); }, [pages]);
  return { rows: rows.slice(page * size, (page + 1) * size), total: rows.length, page, pages, size, setIndex, setSize };
}

type PaginationProps = Omit<ReturnType<typeof useListPage<unknown>>, "rows"> & { label: string; sizeLabel?: string; sizes?: readonly number[] };
export default function ListPagination({total, page, pages, size, setIndex, setSize, label, sizeLabel = label + "每页条数", sizes = [10,20,50]}: PaginationProps) {
  if (!total) return null;
  return <div className="task-pagination list-pagination" role="group" aria-label={label}>
    <span>第 {page * size + 1}–{Math.min(total, (page + 1) * size)} 条，共 {total} 条</span>
    <div><label>每页 <select aria-label={sizeLabel} value={size} onChange={event => setSize(Number(event.target.value))}>
      {sizes.map(n => <option key={n} value={n}>{n}</option>)}
    </select></label><button type="button" className="secondary-button compact" disabled={page === 0} onClick={() => setIndex(page - 1)}>上一页</button>
    <span aria-live="polite">{page + 1}/{pages}</span><button type="button" className="secondary-button compact" disabled={page + 1 >= pages} onClick={() => setIndex(page + 1)}>下一页</button></div>
  </div>;
}
