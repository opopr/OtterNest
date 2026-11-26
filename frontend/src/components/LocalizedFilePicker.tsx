import type { ChangeEventHandler } from "react";

interface Props {
  label: string;
  fileName?: string;
  accept?: string;
  disabled?: boolean;
  onChange: ChangeEventHandler<HTMLInputElement>;
}

/** Visible Chinese labels; native file selection and keyboard interaction remain intact. */
export default function LocalizedFilePicker({label,fileName,accept,disabled,onChange}:Props) {
  return <span className="localized-file-picker">
    <span className="file-picker-button" aria-hidden="true">选择文件</span>
    <span className="file-picker-name" aria-live="polite">{fileName || "未选择文件"}</span>
    <input type="file" aria-label={label} accept={accept} disabled={disabled} onChange={onChange}/>
  </span>;
}
