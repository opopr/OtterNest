import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string | number;
  hint: string;
  icon: LucideIcon;
  tone?: "violet" | "blue" | "green" | "amber";
}

export default function StatCard({ label, value, hint, icon: Icon, tone = "violet" }: StatCardProps) {
  return (
    <section className="stat-card">
      <div className={"stat-icon tone-" + tone}><Icon size={19} strokeWidth={1.8} /></div>
      <div className="stat-copy"><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</div>
    </section>
  );
}
