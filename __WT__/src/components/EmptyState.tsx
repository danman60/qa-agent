import type { ReactNode } from "react";
import { CLS } from "@/lib/contract";

interface EmptyStateProps {
  title: string;
  sub?: string;
  action?: ReactNode;
}

export default function EmptyState({ title, sub, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-300 px-6 py-12 text-center dark:border-zinc-700">
      <p className="text-base font-semibold text-zinc-900 dark:text-zinc-100">{title}</p>
      {sub !== undefined && <p className={`mt-1 max-w-sm ${CLS.mutedText}`}>{sub}</p>}
      {action !== undefined && <div className="mt-4">{action}</div>}
    </div>
  );
}
