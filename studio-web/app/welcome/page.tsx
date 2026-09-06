"use client";

import { useEffect, useRef, useState } from "react";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import { appRelativePath } from "@/lib/urls";
import { ArrowRight, ChevronDown, Check } from "lucide-react";

const HOW = [
  {
    step: "1", title: "Define your workflow",
    desc: "Describe your scientific goal in plain language. The AI translates your intent into a fully configured Pegasus WMS workflow — complete with scripts, dependencies, and execution settings.",
    accent: "#0ea5e9",
  },
  {
    step: "2", title: "Submit with one click",
    desc: "Your workflow is compiled and dispatched automatically — running on your local cluster, your institution's computing infrastructure, or distributed across global shared pools.",
    accent: "#8b5cf6",
  },
  {
    step: "3", title: "Monitor in real time",
    desc: "Track every job as it executes across your pipeline. The AI surfaces failures, diagnoses root causes, and recommends corrective actions before issues cascade.",
    accent: "#10b981",
  },
] as const;

const FEATURES = [
  {
    num: "01", label: "AI Assistant", title: "Natural language workflow authoring",
    bullets: ["Compose pipelines by describing your scientific goals", "Diagnose failures with AI-powered root-cause analysis", "Auto-generate execution scripts and configuration files", "Access expert Pegasus WMS guidance without reading documentation"],
    grad: "from-sky-500/[0.12] to-sky-500/[0.02]", border: "border-sky-500/20", num_c: "text-sky-400/20",
  },
  {
    num: "02", label: "Live Dashboard", title: "Complete pipeline observability",
    bullets: ["Interactive DAG visualization of your full workflow graph", "Per-job execution status updated in real time", "One-click access to logs, stderr output, and exit codes", "Proactive alerts when jobs stall, fail, or exceed resource limits"],
    grad: "from-violet-500/[0.12] to-violet-500/[0.02]", border: "border-violet-500/20", num_c: "text-violet-400/20",
  },
  {
    num: "03", label: "Notebooks", title: "Integrated computational environment",
    bullets: ["Browser-based Jupyter notebooks with zero configuration", "Immediate access to your workflow inputs, outputs, and data", "Python, R, and Julia kernels available on demand", "AI assistant embedded alongside your code for in-context help"],
    grad: "from-emerald-500/[0.12] to-emerald-500/[0.02]", border: "border-emerald-500/20", num_c: "text-emerald-400/20",
  },
  {
    num: "04", label: "Compute Power", title: "Elastic infrastructure at any scale",
    bullets: ["Execute workflows on your local or institutional computing cluster", "Seamlessly burst to OSPool's 19,000+ cores across global sites", "Real-time resource utilization and quota visibility", "Threshold alerts before capacity limits are reached"],
    grad: "from-amber-500/[0.12] to-amber-500/[0.02]", border: "border-amber-500/20", num_c: "text-amber-400/20",
  },
] as const;

export default function WelcomePage() {
  const [m, setM] = useState(false);
  const detailsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setM(true), 80);
    return () => clearTimeout(t);
  }, []);

  const anim = (d: number): React.CSSProperties => ({
    opacity:   m ? 1 : 0,
    transform: m ? "translateY(0)" : "translateY(20px)",
    transition: `opacity 0.55s ease ${d}s, transform 0.55s ease ${d}s`,
  });

  return (
    <div className="bg-base">

      {/* Dot grid */}
      <div className="pointer-events-none fixed inset-0"
        style={{ backgroundImage: "radial-gradient(rgba(14,165,233,0.09) 1px,transparent 1px)", backgroundSize: "28px 28px" }} />
      {/* Top glow */}
      <div className="pointer-events-none fixed inset-x-0 top-0 h-[600px]"
        style={{ background: "radial-gradient(ellipse 90% 55% at 50% -5%,rgba(14,165,233,0.13) 0%,transparent 70%)" }} />

      {/* ══ HERO — full viewport ══════════════════════════════════════════ */}
      <section className="relative flex min-h-screen flex-col items-center justify-center px-6 text-center">

        {/* Logo */}
        <div className="mb-10" style={{
          opacity: m ? 1 : 0, transform: m ? "scale(1)" : "scale(0.8)",
          transition: "opacity 0.7s ease, transform 0.8s cubic-bezier(0.34,1.4,0.64,1)",
        }}>
          <div className="relative flex h-28 w-28 items-center justify-center sm:h-36 sm:w-36">
            <div className="absolute -inset-3 rounded-[44px] border border-accent/8"
              style={{ animation: "spin 14s linear infinite reverse" }} />
            <div className="absolute inset-0 rounded-[32px] border-2 border-line"
              style={{ animation: "spin 8s linear infinite", borderTopColor: "rgb(14 165 233)" }} />
            <div className="absolute inset-4 rounded-3xl bg-accent/8" />
            <div className="relative z-10 flex h-[88px] w-[88px] items-center justify-center rounded-3xl bg-surface shadow-2xl ring-1 ring-line sm:h-28 sm:w-28">
              <PegasusLogo size={56} />
            </div>
          </div>
        </div>

        {/* Title */}
        <div style={anim(0.08)}>
          <span className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/8 px-3.5 py-1 text-[10px] font-bold uppercase tracking-[0.22em] text-accent">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            Scientific Workflow Studio
          </span>
          <h1 className="mt-4 text-4xl font-black leading-tight tracking-tight text-fg sm:text-[58px] sm:leading-none">
            Pegasus<span className="text-accent">AI</span> Studio
          </h1>
        </div>

        {/* Description */}
        <p className="mb-10 mt-5 max-w-md text-base leading-7 text-fgmuted" style={anim(0.16)}>
          Design, execute, and monitor large-scale scientific workflows at any
          scale — with an AI assistant that manages the complexity so you can
          focus on the science.
        </p>

        {/* CTA */}
        <div className="flex flex-col items-center gap-3" style={anim(0.22)}>
          <a href={appRelativePath("/home")}
            className="group inline-flex items-center gap-2.5 rounded-2xl bg-accent px-9 py-4 text-[15px] font-bold text-white shadow-2xl shadow-accent/25 transition-all hover:bg-accent/90 hover:-translate-y-0.5 hover:shadow-accent/35 active:scale-[0.97]">
            Sign In
            <ArrowRight size={16} className="transition-transform group-hover:translate-x-1" />
          </a>
          <p className="text-[11px] text-fgsubtle/50">Free · No setup · Ready in seconds</p>
        </div>

        {/* Scroll arrow */}
        <button
          onClick={() => detailsRef.current?.scrollIntoView({ behavior: "smooth" })}
          className="absolute bottom-8 flex flex-col items-center gap-1.5 text-fgsubtle/40 transition-colors hover:text-fgsubtle/70"
          style={anim(0.4)}
          aria-label="Scroll to learn more"
        >
          <span className="text-[10px] font-medium uppercase tracking-widest">Learn more</span>
          <ChevronDown size={18} className="animate-bounce" />
        </button>
      </section>

      {/* ══ DETAILS — scroll target ═══════════════════════════════════════ */}
      <div ref={detailsRef} className="relative mx-auto max-w-3xl px-4 pb-24 sm:px-8">

        {/* What is it */}
        <div className="mt-4 sm:mt-8">
          <SectionLabel>What is this?</SectionLabel>
          <div className="mt-6 grid gap-4 sm:grid-cols-5">
            <div className="rounded-3xl border border-line bg-surface p-6 sm:col-span-3 sm:p-7">
              <h2 className="mb-3 text-lg font-bold text-fg sm:text-xl">A unified platform for scientific workflow execution</h2>
              <p className="text-sm leading-7 text-fgmuted">
                Research institutions worldwide have depended on <strong className="font-semibold text-fg">Pegasus WMS</strong> for
                over two decades to orchestrate large-scale scientific pipelines — from gravitational
                wave detection and genome sequencing to climate modeling and particle physics.
                PegasusAI Studio brings that power to the browser: describe your pipeline in
                natural language, let the AI generate and configure it, then execute it across
                hundreds or thousands of cores with a single click.
              </p>
            </div>
            <div className="flex flex-col gap-2 sm:col-span-2">
              {["AI-generated workflow authoring and configuration", "Real-time DAG visualization and job monitoring", "Access to 19,000+ cores through OSPool", "Integrated Jupyter notebooks with AI assistance", "Isolated, persistent personal workspace"].map((item) => (
                <div key={item} className="flex items-center gap-2.5 rounded-2xl border border-line bg-surface px-4 py-3">
                  <Check size={13} className="shrink-0 text-accent" strokeWidth={2.5} />
                  <span className="text-[12px] font-medium text-fgmuted">{item}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* How it works */}
        <div className="mt-20">
          <SectionLabel>How it works</SectionLabel>
          <h2 className="mb-8 mt-3 text-xl font-bold text-fg sm:text-2xl">From research goal to running pipeline in three steps</h2>
          <div className="grid gap-3 sm:grid-cols-3">
            {HOW.map((h, i) => (
              <div key={h.step} className="relative rounded-3xl border border-line bg-surface p-6">
                {i < HOW.length - 1 && (
                  <div className="absolute -right-2 top-1/2 z-10 hidden -translate-y-1/2 sm:flex h-5 w-5 items-center justify-center rounded-full border border-line bg-surface">
                    <ArrowRight size={10} className="text-fgsubtle" />
                  </div>
                )}
                <div className="mb-4 flex items-center gap-2.5">
                  <span className="flex h-7 w-7 items-center justify-center rounded-xl text-sm font-black text-white"
                    style={{ background: h.accent }}>{h.step}</span>
                  <span className="text-[15px] font-bold text-fg">{h.title}</span>
                </div>
                <p className="text-[12px] leading-relaxed text-fgmuted">{h.desc}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Features */}
        <div className="mt-20">
          <SectionLabel>Features</SectionLabel>
          <h2 className="mb-8 mt-3 text-xl font-bold text-fg sm:text-2xl">Capabilities built for research at scale</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {FEATURES.map((f) => (
              <div key={f.num}
                className={`relative overflow-hidden rounded-3xl border ${f.border} bg-gradient-to-br ${f.grad} p-6 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg`}>
                <span className={`absolute right-4 top-0 select-none font-black leading-none ${f.num_c}`}
                  style={{ fontSize: 80 }}>{f.num}</span>
                <div className="relative">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-fgsubtle">{f.label}</p>
                  <p className="mb-4 mt-1 text-[15px] font-bold text-fg">{f.title}</p>
                  <ul className="space-y-2">
                    {f.bullets.map((b) => (
                      <li key={b} className="flex items-start gap-2 text-[12px] leading-relaxed text-fgmuted">
                        <Check size={12} className="mt-0.5 shrink-0 text-accent/70" strokeWidth={2.5} />
                        {b}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom CTA */}
        <div className="mt-20">
          <div className="rounded-3xl border border-accent/20 bg-accent/5 px-6 py-10 text-center sm:px-12">
            <h2 className="mb-2 text-xl font-black text-fg sm:text-2xl">Start your first pipeline today</h2>
            <p className="mb-7 text-sm text-fgmuted">Your workspace is provisioned automatically. No installation, no cluster configuration, no prior Pegasus experience required.</p>
            <a href={appRelativePath("/home")}
              className="group inline-flex items-center gap-2.5 rounded-2xl bg-accent px-8 py-4 text-[15px] font-bold text-white shadow-2xl shadow-accent/25 transition-all hover:bg-accent/90 hover:-translate-y-0.5 hover:shadow-accent/35 active:scale-[0.97]">
              Sign In
              <ArrowRight size={16} className="transition-transform group-hover:translate-x-1" />
            </a>
          </div>
          <p className="mt-6 text-center text-[11px] text-fgsubtle/35">
            Powered by{" "}
            <a href="https://pegasus.isi.edu" target="_blank" rel="noopener noreferrer"
              className="underline underline-offset-2 hover:text-fgsubtle/60 transition-colors">Pegasus WMS</a>
            {" · "}Apache 2.0 · Open Source
          </p>
        </div>

      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="h-px flex-1 bg-gradient-to-r from-transparent to-line" />
      <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-accent">{children}</span>
      <span className="h-px flex-1 bg-gradient-to-l from-transparent to-line" />
    </div>
  );
}
