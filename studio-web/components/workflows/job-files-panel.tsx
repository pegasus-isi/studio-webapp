"use client";

/**
 * The files a job reads and writes, opened by clicking it in the graph.
 *
 * The graph shows only jobs, so "which step made this plot, and from what?"
 * meant reading workflow.yml by hand. File nodes in the graph itself would
 * answer it too, but files outnumber jobs two or three to one and the graph is
 * already hard to read at scale; a panel answers the question for the job you
 * are looking at and costs nothing until you click.
 *
 * Every file the job declares is listed, including ones that exist nowhere yet
 * (not produced, or cleaned out of scratch): knowing a job *should* have made
 * a file is half of working out why it didn't.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, X } from "lucide-react";
import type { FileLocation, JobFile, JobFiles } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import {
  FilePreviewDialog,
  FileTypeIcon,
  formatBytes,
  type PreviewTarget,
} from "@/components/workflows/file-preview";

/** How the panel reaches files: per run, or per project before any run. */
export interface JobFilesSource {
  /** Distinguishes cached listings (a run id, a project id). */
  key: string;
  list: (jobId: string) => Promise<JobFiles>;
  /** Files picked by name: a file node, or a group of them, clicked in the
   *  graph with "Show files" on (#458). */
  files: (lfns: string[]) => Promise<JobFiles>;
  url: (file: JobFile, download?: boolean) => string | null;
  /** Poll while the run is going: files appear as jobs finish. */
  live: boolean;
}

export interface PanelJob {
  id: string;
  label: string;
  status: string;
}

const LOCATION: Record<FileLocation, { label: string; title: string; cls: string }> = {
  result: {
    label: "result",
    title: "Staged to the run's output directory; kept after the run.",
    cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-400/10 dark:text-emerald-300",
  },
  scratch: {
    label: "scratch",
    title: "In the run's scratch directory; removed when the run cleans up.",
    cls: "bg-amber-50 text-amber-700 dark:bg-amber-400/10 dark:text-amber-300",
  },
  project: {
    label: "project",
    title: "A local input file in the workflow's project folder.",
    cls: "bg-pegasus-50 text-pegasus-700 dark:bg-pegasus-400/10 dark:text-pegasus-300",
  },
};

export function JobFilesPanel({
  title,
  jobs,
  source,
  onClose,
}: {
  /** The clicked node's label; for a group, the step name. */
  title: string;
  /** One job, or every task of a grouped node. */
  jobs: PanelJob[];
  source: JobFilesSource;
  onClose: () => void;
}) {
  // A group lists its tasks first; a single job opens straight to its files.
  const [selected, setSelected] = useState<PanelJob | null>(
    jobs.length === 1 ? jobs[0] : null
  );
  useEffect(() => {
    setSelected(jobs.length === 1 ? jobs[0] : null);
  }, [jobs]);

  return (
    <aside
      aria-label="Files used by the selected job"
      className="absolute inset-y-0 right-0 z-20 flex w-80 max-w-full flex-col border-l border-line bg-surface shadow-xl"
      // Clicks inside must not reach React Flow's pane and clear the selection.
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start gap-2 border-b border-line px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-fg" title={title}>
            {selected && jobs.length > 1 ? selected.label : title}
          </p>
          <p className="truncate text-xs text-fgsubtle">
            {selected
              ? `${selected.id} · ${selected.status}`
              : `${jobs.length} tasks — pick one to see its files`}
          </p>
        </div>
        {selected && jobs.length > 1 && (
          <button
            onClick={() => setSelected(null)}
            className="flex shrink-0 items-center gap-0.5 rounded px-1.5 py-0.5 text-xs text-fgmuted hover:bg-base hover:text-fg"
          >
            <ChevronLeft size={14} aria-hidden="true" />
            Tasks
          </button>
        )}
        <button
          onClick={onClose}
          aria-label="Close files panel"
          className="shrink-0 rounded p-1 text-fgmuted hover:bg-base hover:text-fg"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {selected ? (
          <JobFileList job={selected} source={source} />
        ) : (
          <ul className="divide-y divide-line">
            {jobs.map((j) => (
              <li key={j.id}>
                <button
                  onClick={() => setSelected(j)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-base"
                >
                  <span className="min-w-0 flex-1 truncate text-sm text-fg">{j.id}</span>
                  <span className="shrink-0 text-xs text-fgsubtle">{j.status}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

function JobFileList({ job, source }: { job: PanelJob; source: JobFilesSource }) {
  const [preview, setPreview] = useState<PreviewTarget | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["job-files", source.key, job.id],
    queryFn: () => source.list(job.id),
    refetchInterval: source.live ? 10_000 : false,
  });

  if (isLoading) return <p className="p-3 text-sm text-fgmuted">Loading files…</p>;
  if (error)
    return (
      <p className="p-3 text-sm text-red-600 dark:text-rose-400">
        Couldn&apos;t list files: {(error as Error).message}
      </p>
    );
  if (!data?.job_found)
    return (
      <p className="p-3 text-sm text-fgmuted">
        This is one of Pegasus&apos;s own jobs (staging, directory setup,
        cleanup). It moves the workflow&apos;s files but declares none of its own.
      </p>
    );

  const inputs = data.files.filter((f) => f.link === "input");
  const outputs = data.files.filter((f) => f.link === "output");

  const open = (f: JobFile) => {
    const url = source.url(f);
    const downloadUrl = source.url(f, true);
    if (!url || !downloadUrl) return;
    setPreview({
      name: f.name,
      path: f.lfn,
      kind: f.kind,
      size: f.size ?? undefined,
      partial: f.partial,
      url,
      downloadUrl,
    });
  };

  return (
    <div className="space-y-3 p-3">
      <FileGroup title="Outputs" files={outputs} source={source} onPreview={open} live={source.live} />
      <FileGroup title="Inputs" files={inputs} source={source} onPreview={open} live={source.live} />
      {preview && <FilePreviewDialog target={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function FileGroup({
  title,
  files,
  source,
  onPreview,
  live,
}: {
  title: string;
  files: JobFile[];
  source: JobFilesSource;
  onPreview: (f: JobFile) => void;
  live: boolean;
}) {
  return (
    <section>
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-fgsubtle">
        {title} <span className="font-normal">({files.length})</span>
      </h3>
      {files.length === 0 ? (
        <p className="text-xs text-fgsubtle">None.</p>
      ) : (
        <ul className="divide-y divide-line rounded-md border border-line">
          {files.map((f) => {
            const where = f.location ? LOCATION[f.location] : null;
            const download = f.location ? source.url(f, true) : null;
            return (
              <li key={f.lfn} className={cn("px-2 py-1.5", !f.location && "opacity-60")}>
                <div className="flex items-center gap-1.5">
                  <FileTypeIcon name={f.name} kind={f.kind} />
                  <span className="min-w-0 flex-1 truncate text-sm text-fg" title={f.lfn}>
                    {f.name}
                  </span>
                  {where && (
                    <span
                      title={where.title}
                      className={cn("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium", where.cls)}
                    >
                      {where.label}
                    </span>
                  )}
                </div>
                {f.lfn !== f.name && (
                  <p className="truncate pl-[22px] text-[11px] text-fgsubtle" title={f.lfn}>
                    {f.lfn}
                  </p>
                )}
                <div className="mt-1 flex items-center gap-2 pl-[22px]">
                  {f.location ? (
                    <>
                      {f.size !== null && (
                        <span className="text-[11px] text-fgsubtle">{formatBytes(f.size)}</span>
                      )}
                      {f.previewable && (
                        <button
                          onClick={() => onPreview(f)}
                          className="rounded border border-line px-1.5 py-0.5 text-[11px] text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
                        >
                          Preview
                        </button>
                      )}
                      {download && (
                        <a
                          href={download}
                          download={f.name}
                          className="rounded border border-line px-1.5 py-0.5 text-[11px] text-fgmuted hover:border-pegasus-400 hover:text-pegasus-700 dark:hover:text-pegasus-300"
                        >
                          Download
                        </a>
                      )}
                    </>
                  ) : (
                    <span className="text-[11px] text-fgsubtle">
                      {f.link === "output"
                        ? live
                          ? "Not produced yet"
                          : f.stage_out
                            ? "Not in the results"
                            : "Temporary; not kept after the run"
                        : "Not stored locally (fetched when the job runs)"}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}


/**
 * A file node (or a group of them) clicked in the graph. Same rows as a job's
 * file list, so a file looks and opens the same from either entry point.
 */
export function FileNodePanel({
  title,
  lfns,
  source,
  onClose,
}: {
  title: string;
  lfns: string[];
  source: JobFilesSource;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<PreviewTarget | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["file-info", source.key, ...lfns],
    queryFn: () => source.files(lfns),
    refetchInterval: source.live ? 10_000 : false,
  });

  const open = (f: JobFile) => {
    const url = source.url(f);
    const downloadUrl = source.url(f, true);
    if (!url || !downloadUrl) return;
    setPreview({
      name: f.name,
      path: f.lfn,
      kind: f.kind,
      size: f.size ?? undefined,
      partial: f.partial,
      url,
      downloadUrl,
    });
  };

  return (
    <aside
      aria-label="Selected files"
      className="absolute inset-y-0 right-0 z-20 flex w-80 max-w-full flex-col border-l border-line bg-surface shadow-xl"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start gap-2 border-b border-line px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-fg" title={title}>
            {title}
          </p>
          <p className="truncate text-xs text-fgsubtle">
            {lfns.length === 1 ? lfns[0] : `${lfns.length} files`}
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close files panel"
          className="shrink-0 rounded p-1 text-fgmuted hover:bg-base hover:text-fg"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {isLoading ? (
          <p className="text-sm text-fgmuted">Loading…</p>
        ) : error ? (
          <p className="text-sm text-red-600 dark:text-rose-400">
            Couldn&apos;t look the file up: {(error as Error).message}
          </p>
        ) : (
          <FileGroup
            title={lfns.length === 1 ? "File" : "Files"}
            files={data?.files ?? []}
            source={source}
            onPreview={open}
            live={source.live}
          />
        )}
      </div>
      {preview && <FilePreviewDialog target={preview} onClose={() => setPreview(null)} />}
    </aside>
  );
}
