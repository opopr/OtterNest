import { getTaskStatusPresentation } from "../utils/labels";
export default function TaskStatusBadge({status}: {status:string}) {
  const {label,tone} = getTaskStatusPresentation(status);
  return <span className={"status-tag " + tone} data-task-status={status}>{label}</span>;
}
