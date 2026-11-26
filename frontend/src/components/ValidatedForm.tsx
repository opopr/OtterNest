import { useEffect, useId, useState, type FormEvent, type FormHTMLAttributes } from "react";
import { AlertCircle } from "lucide-react";
import { constraintMessage } from "../utils/formValidation";

type Field = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
function isField(target: EventTarget | null): target is Field {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
}
function fieldMessage(field: Field) {
  const label = field.getAttribute("aria-label") || Array.from(field.labels?.[0]?.childNodes || [])
    .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent || "").join(" ").trim();
  return constraintMessage(field.validity, {
    label, type: field instanceof HTMLSelectElement ? "select" : field.type,
    minLength: field instanceof HTMLSelectElement ? undefined : field.minLength,
    maxLength: field instanceof HTMLSelectElement ? undefined : field.maxLength,
    min: field instanceof HTMLInputElement ? field.min : undefined,
    max: field instanceof HTMLInputElement ? field.max : undefined,
  });
}

/** Keep native HTML constraints; replace browser-language bubbles with one Chinese, accessible error. */
export default function ValidatedForm({children, onSubmit, onInvalidCapture, onInputCapture, onChangeCapture, onReset, ...props}: FormHTMLAttributes<HTMLFormElement>) {
  const id = useId();
  const [issue, setIssue] = useState<{field: Field; message: string} | null>(null);
  const field = issue?.field;
  useEffect(() => {
    if (!field) return;
    const previousInvalid = field.getAttribute("aria-invalid");
    const previousDescription = field.getAttribute("aria-describedby");
    field.setAttribute("aria-invalid", "true");
    field.setAttribute("aria-describedby", [previousDescription, id].filter(Boolean).join(" "));
    return () => {
      if (previousInvalid === null) field.removeAttribute("aria-invalid"); else field.setAttribute("aria-invalid", previousInvalid);
      if (previousDescription === null) field.removeAttribute("aria-describedby"); else field.setAttribute("aria-describedby", previousDescription);
    };
  }, [field,id]);
  const updateIssue = (event: FormEvent<HTMLFormElement>) => {
    if (isField(event.target) && issue?.field === event.target) {
      const current = event.target;
      setIssue(current.validity.valid ? null : {field: current, message: fieldMessage(current)});
    }
  };
  return <form {...props}
    onInvalidCapture={event => {
      onInvalidCapture?.(event);
      if (event.defaultPrevented || !isField(event.target)) return;
      event.preventDefault();
      // Browsers may run a microtask between invalid events. Always select the
      // first invalid form control in DOM order rather than the latest event.
      const current = Array.from(event.currentTarget.elements).find(
        (element): element is Field => isField(element) && element.willValidate && !element.validity.valid
      ) ?? event.target;
      setIssue({field: current, message: fieldMessage(current)});
      current.focus();
    }}
    onInputCapture={event => { onInputCapture?.(event); updateIssue(event); }}
    onChangeCapture={event => { onChangeCapture?.(event); updateIssue(event); }}
    onReset={event => { setIssue(null); onReset?.(event); }}
    onSubmit={event => { setIssue(null); onSubmit?.(event); }}>
    {issue && <div id={id} className="form-validation-message" role="alert"><AlertCircle size={15} aria-hidden="true"/><span>{issue.message}</span></div>}
    {children}
  </form>;
}
