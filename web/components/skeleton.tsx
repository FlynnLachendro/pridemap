export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden />;
}
export function CardSkeleton() {
  return (
    <div className="card p-4 grid gap-3">
      <div className="flex gap-3"><Skeleton className="h-3 w-20" /><Skeleton className="h-3 w-10" /><Skeleton className="h-3 w-24" /></div>
      <Skeleton className="h-5 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
      <div className="flex gap-2"><Skeleton className="h-5 w-16 rounded-full" /><Skeleton className="h-5 w-20 rounded-full" /><Skeleton className="h-5 w-14 rounded-full" /></div>
    </div>
  );
}
export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`spinner ${className}`} aria-hidden />;
}
