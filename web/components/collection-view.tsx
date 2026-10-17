"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { API, deleteCollection, getCollection, patchCollection, pretty, TAG_LABEL, type Collection } from "@/lib/api";
import { Skeleton } from "@/components/skeleton";

const SHOW_TAGS = ["organism", "tissue", "enrichment", "acquisition", "labelling"];

export function CollectionView() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [c, setC] = useState<Collection | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    getCollection(id).then((r) => { if (!live) return; setC(r); setName(r.name); setNote(r.note ?? ""); }).catch((e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [id]);

  const save = async (body: { name?: string; note?: string; accessions?: string[] }) => {
    try { const r = await patchCollection(id, body); setC(r); setName(r.name); setNote(r.note ?? ""); } catch (e) { setErr(String(e)); }
  };
  const openInFlow = () => {
    if (!c) return;
    try { localStorage.setItem("pridemap.compare", JSON.stringify(c.accessions)); } catch {}
    router.push("/flow");
  };
  const share = async () => {
    try { await navigator.clipboard.writeText(window.location.href); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch {}
  };

  if (err) return <p className="text-warn text-sm">{err.includes("404") ? "Unknown collection." : `API error: ${err}`}</p>;
  if (!c) return <div className="grid gap-3 max-w-4xl"><Skeleton className="h-9 w-1/2" /><Skeleton className="h-20" /><Skeleton className="h-20" /></div>;

  return (
    <div className="grid gap-5 max-w-4xl">
      <div className="grid gap-2">
        <div className="flex items-center gap-3 text-sm">
          <Link href="/collections" className="text-muted-foreground hover:underline">← Collections</Link>
          <span className="font-mono text-xs text-faint">{c.id}</span>
          <span className="text-xs text-faint tabular">updated {c.updated_at.slice(0, 10)}</span>
        </div>
        {editing ? (
          <form className="flex gap-2 items-center" onSubmit={(e) => { e.preventDefault(); setEditing(false); if (name.trim() && name.trim() !== c.name) save({ name: name.trim() }); else setName(c.name); }}>
            <input value={name} onChange={(e) => setName(e.target.value)} className="input h-10 text-2xl font-heading max-w-lg" autoFocus onBlur={() => { setEditing(false); if (name.trim() && name.trim() !== c.name) save({ name: name.trim() }); else setName(c.name); }} />
            <button type="submit" className="btn btn-sm">Save</button>
          </form>
        ) : (
          <h1 className="text-3xl sm:text-4xl flex items-baseline gap-3 flex-wrap">
            <span>{c.name}</span>
            <button type="button" className="text-xs underline text-muted-foreground font-sans font-normal tracking-normal" onClick={() => setEditing(true)}>rename</button>
          </h1>
        )}
        <textarea value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => { if ((note.trim() || "") !== (c.note ?? "")) save({ note: note.trim() }); }} rows={2} className="input max-w-2xl text-sm" placeholder="Note" aria-label="note" />
      </div>

      <div className="flex flex-wrap gap-2">
        {c.accessions.length >= 2 && <Link href={`/compare?a=${c.accessions.join(",")}`} className="btn">Compare all</Link>}
        <button type="button" className="btn btn-primary" onClick={openInFlow} disabled={!c.accessions.length}>Open in Workflow →</button>
        <a href={`${API}/collections/${c.id}/export.sdrf.tsv`} download={`${c.id}.sdrf.tsv`} className="btn">Export SDRF</a>
        <a href={`${API}/collections/${c.id}/download.sh`} download={`${c.id}-download.sh`} className="btn">Download script (.sh)</a>
        <a href={`${API}/collections/${c.id}/accessions.txt`} download={`${c.id}-accessions.txt`} className="btn btn-ghost">Accessions (.txt)</a>
        <button type="button" className="btn" onClick={share}>{copied ? "Link copied ✓" : "Copy share link"}</button>
        <button type="button" className="btn btn-ghost ml-auto" onClick={async () => { if (!confirm(`Delete "${c.name}"?`)) return; await deleteCollection(c.id); router.push("/collections"); }}>Delete collection</button>
      </div>

      <h2 className="text-xl tabular">{c.n} dataset{c.n === 1 ? "" : "s"}</h2>
      {c.datasets.length === 0 && <p className="text-muted-foreground">This collection is empty.</p>}
      <div className="grid gap-2">
        {c.datasets.map((d) => (
          <article key={d.accession} className="card p-4 grid gap-1.5 enter">
            <div className="flex items-baseline gap-3 flex-wrap text-xs">
              <Link href={`/dataset/${d.accession}`} className="font-mono text-sm text-teal hover:underline">{d.accession}</Link>
              {!d.known && <span className="pill text-[10px]">not in PRIDE Map</span>}
              {d.known && !d.tagged && <span className="pill text-[10px]">not tagged yet</span>}
              {d.publication_date && <span className="text-faint tabular">{d.publication_date.slice(0, 4)}</span>}
              {d.downloads ? <span className="text-faint tabular">{d.downloads.toLocaleString()} downloads</span> : null}
              <a href={`https://www.ebi.ac.uk/pride/archive/projects/${d.accession}`} target="_blank" rel="noreferrer" className="underline text-muted-foreground">PRIDE ↗</a>
              <button type="button" className="ml-auto underline text-muted-foreground hover:text-foreground" onClick={() => save({ accessions: c.accessions.filter((a) => a !== d.accession) })}>Remove from collection</button>
            </div>
            {d.title && <h3 className="text-base leading-snug"><Link href={`/dataset/${d.accession}`} className="hover:underline">{d.summary ?? d.title}</Link></h3>}
            {d.tagged && (
              <div className="flex flex-wrap gap-1.5 text-xs">
                {SHOW_TAGS.map((k) => d.tags[k] ? <span key={k} className="pill"><span className="opacity-60">{TAG_LABEL[k]}</span> {pretty(d.tags[k])}</span> : null)}
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
