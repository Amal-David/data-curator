import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Braces,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Database,
  FileJson2,
  FileText,
  FlaskConical,
  FolderOpen,
  GitBranch,
  History,
  Layers3,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  WandSparkles,
  X,
} from "lucide-react";
import { api, downloadJson, post } from "./api";
import type {
  Dataset,
  Decision,
  Recipe,
  RecordItem,
  Role,
  Run,
  SavedRecipe,
} from "./types";

const stages = [
  {
    name: "Clean",
    icon: ShieldCheck,
    description: "Keep the signal. Remove the noise.",
    color: "violet",
  },
  {
    name: "Calibrate",
    icon: SlidersHorizontal,
    description: "Align your data with your task.",
    color: "blue",
  },
  {
    name: "Create",
    icon: Sparkles,
    description: "Rephrase with source lineage.",
    color: "pink",
  },
  {
    name: "Compose",
    icon: Layers3,
    description: "Build a reproducible mixture.",
    color: "green",
  },
] as const;
const roleDescriptions: Record<Role, string> = {
  source: "Candidate training data",
  target: "Calibration reference only",
  holdout: "Evaluation exclusion only",
};
const active = (run: Run | null) =>
  !!run && ["queued", "running"].includes(run.status);
const number = (n: unknown) =>
  typeof n === "number" ? n.toLocaleString() : "—";
const short = (s?: string) =>
  s ? (s.length > 16 ? `${s.slice(0, 8)}…${s.slice(-6)}` : s) : "—";
const readable = (s: string) => s.replaceAll("_", " ").replaceAll("-", " ");
const date = (s: string) =>
  new Date(s).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

function Dialog({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    if (!dialog.current?.contains(previous)) dialog.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
      if (e.key === "Tab") {
        const controls = dialog.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href]",
        );
        if (!controls?.length) return;
        const first = controls[0],
          last = controls[controls.length - 1];
        if (
          document.activeElement === dialog.current ||
          !dialog.current?.contains(document.activeElement)
        ) {
          e.preventDefault();
          (e.shiftKey ? last : first).focus();
        } else if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialog}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`dialog ${wide ? "dialog-wide" : ""}`}
      >
        <div className="dialog-header">
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function NumberField({
  label,
  help,
  value,
  onChange,
  min,
  max,
  step = 1,
}: {
  label: string;
  help?: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        value={Number.isNaN(value) ? "" : value}
        onChange={(e) =>
          onChange(e.target.value === "" ? NaN : Number(e.target.value))
        }
        min={min}
        max={max}
        step={step}
      />
      {help && <small>{help}</small>}
    </label>
  );
}

function validate(recipe: Recipe, datasets: Dataset[]): string | null {
  if (!datasets.some((d) => d.role === "source"))
    return "Select at least one source dataset to build a mixture.";
  const values = [
    recipe.seed,
    ...Object.values(recipe.clean),
    recipe.calibrate.quality_min,
    recipe.calibrate.relevance_min,
    recipe.create.max_records,
    recipe.create.temperature,
    recipe.compose.max_records,
    ...Object.values(recipe.compose.weights),
  ];
  if (values.some((n) => !Number.isFinite(n)))
    return "Every numeric setting must contain a finite number.";
  if (!Number.isInteger(recipe.seed)) return "The seed must be an integer.";
  if (
    recipe.clean.min_chars < 0 ||
    recipe.clean.max_chars < recipe.clean.min_chars
  )
    return "Maximum characters must be at least the minimum characters.";
  if (
    recipe.clean.dedup_threshold < 0 ||
    recipe.clean.dedup_threshold > 1 ||
    recipe.clean.holdout_overlap < 0 ||
    recipe.clean.holdout_overlap > 1
  )
    return "Similarity and overlap thresholds must be between 0 and 1.";
  if (
    !Number.isInteger(recipe.clean.holdout_ngram) ||
    recipe.clean.holdout_ngram < 1
  )
    return "Holdout n-gram size must be a positive integer.";
  if (
    [recipe.calibrate.quality_min, recipe.calibrate.relevance_min].some(
      (n) => n < 0 || n > 1,
    )
  )
    return "Quality and relevance thresholds must be between 0 and 1.";
  if (
    !Number.isInteger(recipe.compose.max_records) ||
    recipe.compose.max_records < 1
  )
    return "The composition budget must be a positive whole number of records.";
  const weights = datasets
    .filter((d) => d.role === "source")
    .map((d) => recipe.compose.weights[d.id] ?? 1);
  if (weights.some((w) => w < 0) || weights.every((w) => w === 0))
    return "Source weights must be nonnegative with at least one positive weight.";
  if (recipe.create.enabled && !recipe.create.model.trim())
    return "Enter a local model name, or disable synthetic generation.";
  if (
    recipe.create.enabled &&
    (!Number.isInteger(recipe.create.max_records) ||
      recipe.create.max_records < 1)
  )
    return "Generation budget must be a positive whole number.";
  return null;
}

export default function App() {
  const [page, setPage] = useState<"studio" | "datasets" | "runs">("studio");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [saved, setSaved] = useState<SavedRecipe[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [stage, setStage] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [inspecting, setInspecting] = useState<Dataset | null>(null);
  const [showResults, setShowResults] = useState(false);
  const [resultTab, setResultTab] = useState<
    "records" | "decisions" | "manifest"
  >("records");
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState("");
  const [resultsLoading, setResultsLoading] = useState(false);
  const [record, setRecord] = useState<RecordItem | null>(null);
  const [search, setSearch] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [recipeName, setRecipeName] = useState("");
  const selectedDatasets = datasets.filter((d) => selected.includes(d.id));
  const sources = selectedDatasets.filter((d) => d.role === "source");
  const working = active(run);

  const refresh = useCallback(async (initial = false) => {
    const [ds, rs, recipes] = await Promise.all([
      api<{ datasets: Dataset[] }>("/datasets"),
      api<{ runs: Run[] }>("/runs"),
      api<{ recipes: SavedRecipe[] }>("/recipes"),
    ]);
    setDatasets(ds.datasets);
    setRuns(rs.runs);
    setSaved(recipes.recipes);
    setConnected(true);
    if (initial) {
      setSelected(ds.datasets.map((d) => d.id));
      const current = rs.runs.find((r) => active(r));
      if (current) {
        setRun(current);
        setShowResults(true);
      }
    }
  }, []);
  const initialize = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [defaultRecipe] = await Promise.all([
        api<Recipe>("/recipe/default"),
        refresh(true),
      ]);
      setRecipe(defaultRecipe);
    } catch (e) {
      setError((e as Error).message);
      setConnected(false);
    } finally {
      setLoading(false);
    }
  }, [refresh]);
  useEffect(() => {
    void initialize();
  }, [initialize]);
  useEffect(() => {
    if (!working || !run) return;
    let live = true;
    const timer = setInterval(async () => {
      try {
        const latest = await api<Run>(`/runs/${run.id}`);
        if (live) {
          setRun(latest);
          if (!active(latest)) {
            void refresh().catch((e) => setError(e.message));
            if (latest.status === "completed")
              setNotice(
                "Run complete. Every selection and rejection is ready to inspect.",
              );
          }
        }
      } catch (e) {
        if (live) {
          setError(`Run status unavailable: ${(e as Error).message}`);
        }
      }
    }, 650);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [working, run?.id, refresh]);
  useEffect(() => {
    if (!run || run.status !== "completed" || !showResults) return;
    let live = true;
    setResultsLoading(true);
    const path =
      resultTab === "records"
        ? `/runs/${run.id}/records?offset=${offset}&limit=25`
        : resultTab === "decisions"
          ? `/runs/${run.id}/decisions?offset=${offset}&limit=25${action ? `&action=${action}` : ""}`
          : `/runs/${run.id}/manifest`;
    api<Record<string, unknown>>(path)
      .then((data) => {
        if (!live) return;
        if (resultTab === "records") {
          setRecords(data.records as RecordItem[]);
          setTotal(data.total as number);
        } else if (resultTab === "decisions") {
          setDecisions(data.decisions as Decision[]);
          setTotal(data.total as number);
        } else
          setRun((current) =>
            current?.id === run.id ? { ...current, manifest: data } : current,
          );
      })
      .catch((e) => {
        if (live) setError(e.message);
      })
      .finally(() => {
        if (live) setResultsLoading(false);
      });
    return () => {
      live = false;
    };
  }, [run?.id, run?.status, showResults, resultTab, offset, action]);

  async function perform(name: string, fn: () => Promise<void>) {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function loadDemo() {
    await perform("demo", async () => {
      const data = await post<{ datasets: Dataset[] }>("/demo");
      await refresh();
      setSelected((current) => [
        ...new Set([...current, ...data.datasets.map((d) => d.id)]),
      ]);
      setNotice(
        "Demo datasets are ready: original text, code, calibration targets, and held-out evaluation examples.",
      );
    });
  }
  async function startRun() {
    if (!recipe) return;
    const problem = validate(recipe, selectedDatasets);
    if (problem) {
      setError(problem);
      return;
    }
    await perform("run", async () => {
      const weights = Object.fromEntries(
        sources.map((d) => [d.id, recipe.compose.weights[d.id] ?? 1]),
      );
      const started = await post<Run>("/runs", {
        dataset_ids: selected,
        recipe: { ...recipe, compose: { ...recipe.compose, weights } },
      });
      setRun(started);
      setShowResults(true);
      setOffset(0);
      setResultTab("records");
      setPage("studio");
      await refresh();
    });
  }
  async function inspectDataset(d: Dataset) {
    await perform(`inspect-${d.id}`, async () => {
      setInspecting(await api<Dataset>(`/datasets/${d.id}?limit=25`));
    });
  }
  async function openRun(r: Run) {
    await perform(`run-${r.id}`, async () => {
      setRun(await api<Run>(`/runs/${r.id}`));
      setShowResults(true);
      setPage("studio");
      setOffset(0);
      setResultTab("records");
    });
  }
  function changeRecipe<K extends keyof Recipe>(key: K, value: Recipe[K]) {
    if (recipe) setRecipe({ ...recipe, [key]: value });
  }
  function toggleDataset(id: string) {
    setSelected((current) =>
      current.includes(id) ? current.filter((i) => i !== id) : [...current, id],
    );
  }
  function switchResultTab(tab: typeof resultTab) {
    setResultTab(tab);
    setOffset(0);
  }
  const closeUpload = useCallback(() => setUploadOpen(false), []);
  const closeInspector = useCallback(() => setInspecting(null), []);
  const closeRecord = useCallback(() => setRecord(null), []);
  const closeSave = useCallback(() => setSaveOpen(false), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const summary = run?.summary || {};
  const filteredDatasets = datasets.filter((d) =>
    `${d.name} ${d.role}`.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          href="#"
          className="brand"
          onClick={(e) => {
            e.preventDefault();
            setPage("studio");
          }}
          aria-label="Data Curator home"
        >
          <span className="brand-mark">
            <Layers3 size={22} strokeWidth={2.4} />
          </span>
          <span>
            data<span className="brand-light">curator</span>
            <small>THE LOCAL DATA STUDIO</small>
          </span>
        </a>
        <div className="workspace-badge">
          <span className="workspace-avatar">L</span>
          <div>
            Local workspace<small>Personal · Open source</small>
          </div>
          <LockKeyhole size={14} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          <button
            aria-label="Curation studio"
            aria-current={page === "studio" ? "page" : undefined}
            title="Curation studio"
            className={page === "studio" ? "nav-item selected" : "nav-item"}
            onClick={() => setPage("studio")}
          >
            <LayoutDashboard size={19} />
            <span>Curation studio</span>
            <span className="nav-dot" />
          </button>
          <button
            aria-label="Datasets"
            aria-current={page === "datasets" ? "page" : undefined}
            title="Datasets"
            className={page === "datasets" ? "nav-item selected" : "nav-item"}
            onClick={() => setPage("datasets")}
          >
            <Database size={19} />
            <span>Datasets</span>
            <span className="nav-count">{datasets.length}</span>
          </button>
          <button
            aria-label="Run history"
            aria-current={page === "runs" ? "page" : undefined}
            title="Run history"
            className={page === "runs" ? "nav-item selected" : "nav-item"}
            onClick={() => setPage("runs")}
          >
            <History size={19} />
            <span>Run history</span>
            <span className="nav-count">{runs.length}</span>
          </button>
        </nav>
        <div className="sidebar-note">
          <div className="note-orbit">
            <GitBranch size={28} />
          </div>
          <h3>
            Good data.
            <br />
            By design.
          </h3>
          <p>
            Every decision traceable.
            <br />
            Every mixture reproducible.
          </p>
          <button onClick={() => setHelpOpen(true)}>
            How it works <ArrowUpRight size={15} />
          </button>
        </div>
        <div className="sidebar-bottom">
          <div className="privacy-line">
            <ShieldCheck size={16} /> Your data stays here
          </div>
          <p>Local processing · No telemetry</p>
          <button className="help-button" onClick={() => setHelpOpen(true)}>
            <CircleHelp size={17} /> About this studio{" "}
            <ArrowUpRight size={14} />
          </button>
          <div className="version">
            OPEN SOURCE <span>v0.1</span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <ChevronRight size={14} />{" "}
            <span>
              {page === "studio"
                ? "Curation studio"
                : page === "datasets"
                  ? "Datasets"
                  : "Run history"}
            </span>
          </div>
          <div className="topbar-right">
            <span className={`connection ${connected ? "" : "offline"}`}>
              <i />
              {connected
                ? "Local engine connected"
                : loading
                  ? "Connecting to engine"
                  : "Engine unavailable"}
            </span>
            <span className="topbar-divider" />
            <span className="avatar">LC</span>
          </div>
        </header>
        <main id="main-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                {page === "studio"
                  ? "FROM RAW DATA TO READY-TO-USE"
                  : page === "datasets"
                    ? "KNOW WHAT GOES IN"
                    : "A RECORD OF EVERY RECIPE"}
              </div>
              <h1>
                {page === "studio"
                  ? "Make every record count."
                  : page === "datasets"
                    ? "Your data, organized."
                    : "Built to be reproducible."}
              </h1>
              <p>
                {page === "studio"
                  ? "Clean, calibrate, and compose a dataset you can stand behind."
                  : page === "datasets"
                    ? "Inspect immutable inputs and give each dataset a clear purpose."
                    : "Revisit the inputs, decisions, and artifacts behind every run."}
              </p>
            </div>
            <div className="heading-actions">
              <button
                className="button secondary"
                onClick={() => setUploadOpen(true)}
                disabled={!connected || !!busy}
              >
                <Plus size={17} /> Import data
              </button>
              {page === "studio" && (
                <button
                  className="button primary"
                  onClick={startRun}
                  disabled={!recipe || !!busy || working || loading}
                >
                  {busy === "run" ? (
                    <LoaderCircle className="spin" size={17} />
                  ) : (
                    <ArrowRight size={17} />
                  )}{" "}
                  Run pipeline
                </button>
              )}
            </div>
          </div>
          {error && (
            <div className="alert error" role="alert">
              <CircleHelp size={18} />
              <div>
                <strong>Something needs your attention</strong>
                <p>{error}</p>
                {!connected && (
                  <button
                    className="text-button"
                    onClick={() => void initialize()}
                  >
                    Reconnect to local engine
                  </button>
                )}
              </div>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError("")}
              >
                <X size={17} />
              </button>
            </div>
          )}
          {notice && (
            <div className="alert success" role="status">
              <CheckCheck size={18} />
              <span>{notice}</span>
              <button
                className="icon-button"
                aria-label="Dismiss notification"
                onClick={() => setNotice("")}
              >
                <X size={17} />
              </button>
            </div>
          )}
          {loading && (
            <div className="loading-block" role="status">
              <LoaderCircle className="spin" /> Connecting to your local
              workspace…
            </div>
          )}
          {!loading && page === "studio" && (
            <>
              <section className="stage-strip" aria-label="Pipeline stages">
                {stages.map((s, i) => (
                  <button
                    key={s.name}
                    className={`stage-card ${stage === i && !showResults ? "stage-active" : ""}`}
                    onClick={() => {
                      setStage(i);
                      setShowResults(false);
                    }}
                  >
                    <div className={`stage-icon ${s.color}`}>
                      <s.icon size={22} />
                    </div>
                    <div>
                      <div className="stage-title">
                        <span className="stage-number">0{i + 1}</span>
                        {s.name}
                        {i === 2 && <span className="tiny-tag">OPTIONAL</span>}
                      </div>
                      <p>{s.description}</p>
                    </div>
                    <ChevronRight size={16} className="stage-chevron" />
                  </button>
                ))}
              </section>
              {run && showResults ? (
                <section className="results-area">
                  <div className="section-heading">
                    <div className="section-title">
                      <Activity size={19} />
                      <h2>Run workspace</h2>
                      <span className={`status status-${run.status}`}>
                        {readable(run.status)}
                      </span>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => setShowResults(false)}
                    >
                      <Settings2 size={16} /> Back to recipe
                    </button>
                  </div>
                  <div className="run-banner">
                    <div>
                      <span className="eyebrow">
                        {run.recipe?.name || "Curation run"}
                      </span>
                      <h3>
                        {working
                          ? "Your data is taking shape."
                          : run.status === "completed"
                            ? "A clearer dataset. A complete paper trail."
                            : run.status === "cancelled"
                              ? "Run cancelled."
                              : "This run needs attention."}
                      </h3>
                      <p>
                        {working
                          ? run.progress?.message ||
                            "Preparing immutable input snapshots…"
                          : run.status === "completed"
                            ? `Completed ${date(run.created_at)} · Seed ${run.recipe.seed} · ${short(run.id)}`
                            : run.error ||
                              "No completed output was published. You can retry this recipe."}
                      </p>
                    </div>
                    <div>
                      {working ? (
                        <button
                          className="button secondary"
                          onClick={() =>
                            void perform("cancel", async () => {
                              setRun(await post<Run>(`/runs/${run.id}/cancel`));
                              setNotice(
                                "Cancellation requested. The worker will stop at its next safe checkpoint.",
                              );
                            })
                          }
                          disabled={!!busy}
                        >
                          <Square size={15} /> Cancel run
                        </button>
                      ) : (
                        <button
                          className="button secondary"
                          onClick={() =>
                            void perform("retry", async () => {
                              const next = await post<Run>(
                                `/runs/${run.id}/retry`,
                              );
                              setRun(next);
                              setOffset(0);
                              await refresh();
                            })
                          }
                          disabled={!!busy}
                        >
                          <RefreshCw size={16} /> Retry run
                        </button>
                      )}
                    </div>
                  </div>
                  {working && (
                    <div className="progress-panel" role="status">
                      <div className="progress-label">
                        <span>
                          <LoaderCircle className="spin" size={16} />{" "}
                          {readable(run.progress?.stage || "queued")}
                        </span>
                        <span>
                          {run.progress?.total
                            ? `${number(run.progress.completed)} / ${number(run.progress.total)}`
                            : "Working locally"}
                        </span>
                      </div>
                      <div className="progress-track">
                        <div
                          style={{
                            width: run.progress?.total
                              ? `${Math.max(4, Math.min(100, (run.progress.completed / run.progress.total) * 100))}%`
                              : "15%",
                          }}
                        />
                      </div>
                      <p>
                        Cancellation keeps your original datasets intact. No
                        export is published until the run completes.
                      </p>
                    </div>
                  )}
                  {run.status === "completed" && (
                    <>
                      <div className="metric-grid">
                        <Metric
                          label="Source records"
                          value={number(summary.input_records)}
                          detail="Before curation"
                          icon={<Database size={18} />}
                        />
                        <Metric
                          label="Curated records"
                          value={number(summary.output_records)}
                          detail="Selected without replacement"
                          icon={<CheckCheck size={18} />}
                          highlight
                        />
                        <Metric
                          label="Rejected records"
                          value={number(summary.rejected_records)}
                          detail="With inspectable reasons"
                          icon={<ShieldCheck size={18} />}
                        />
                        <Metric
                          label="Estimated tokens"
                          value={number(summary.estimated_tokens_after)}
                          detail={`${number(summary.estimated_tokens_before)} before curation`}
                          icon={<Braces size={18} />}
                        />
                      </div>
                      <div className="result-overview">
                        <div className="panel stage-summary">
                          <div className="panel-header">
                            <h3>Stage by stage</h3>
                            <span className="muted">Actual run counts</span>
                          </div>
                          {Array.isArray(summary.stages) ? (
                            (
                              summary.stages as {
                                stage: string;
                                input: number;
                                output: number;
                                description: string;
                              }[]
                            ).map((s, i) => (
                              <div className="summary-stage" key={s.stage}>
                                <span
                                  className={`mini-stage ${stages[i]?.color || "violet"}`}
                                >
                                  {i + 1}
                                </span>
                                <div>
                                  <strong>{readable(s.stage)}</strong>
                                  <p>{s.description}</p>
                                </div>
                                <span className="stage-count">
                                  {number(s.input)} <ArrowRight size={12} />{" "}
                                  <strong>{number(s.output)}</strong>
                                </span>
                              </div>
                            ))
                          ) : (
                            <p className="empty-small">
                              Stage counts are available in the manifest.
                            </p>
                          )}
                        </div>
                        <div className="panel export-panel">
                          <div className="panel-header">
                            <h3>Take it from here</h3>
                            <ArrowDownToLine size={18} />
                          </div>
                          <p>Your curated data and the evidence behind it.</p>
                          <a
                            className="button primary export-main"
                            href={`/api/runs/${run.id}/export`}
                            download
                          >
                            <ArrowDownToLine size={17} /> Export curated JSONL
                          </a>
                          <div className="export-links">
                            <a href={`/api/runs/${run.id}/rejected`} download>
                              Rejected JSONL <ArrowDownToLine size={14} />
                            </a>
                            <a href={`/api/runs/${run.id}/manifest`} download>
                              Run manifest <ArrowDownToLine size={14} />
                            </a>
                            <a href={`/api/runs/${run.id}/recipe`} download>
                              Recipe <ArrowDownToLine size={14} />
                            </a>
                            <a href={`/api/runs/${run.id}/report`} download>
                              Summary report <ArrowDownToLine size={14} />
                            </a>
                          </div>
                          <div className="export-note">
                            <GitBranch size={15} />
                            <span>
                              Input hashes, seed, lineage, and realized mixture
                              are preserved.
                            </span>
                          </div>
                        </div>
                      </div>
                      {Array.isArray(summary.mixture) && (
                        <section
                          className="panel mixture-panel"
                          aria-label="Realized mixture"
                        >
                          <div className="panel-header">
                            <div>
                              <h3>The mixture you actually built</h3>
                              <p>
                                Requested proportions alongside actual
                                selection. No replacement.
                              </p>
                            </div>
                            <span className="count-badge">
                              {number(summary.output_records)} /{" "}
                              {number(summary.requested_records)} records
                            </span>
                          </div>
                          {(
                            summary.mixture as {
                              dataset_id: string;
                              requested_fraction: number;
                              realized_fraction: number;
                              realized_records: number;
                              available_records: number;
                            }[]
                          ).map((item, i) => (
                            <div className="mixture-row" key={item.dataset_id}>
                              <div className="mixture-source">
                                <span
                                  className={`mixture-dot mixture-color-${i % 3}`}
                                />
                                <strong>
                                  {datasets.find(
                                    (d) => d.id === item.dataset_id,
                                  )?.name || short(item.dataset_id)}
                                </strong>
                              </div>
                              <div className="mixture-bars">
                                <div>
                                  <span
                                    style={{
                                      width: `${item.requested_fraction * 100}%`,
                                    }}
                                    className="requested-bar"
                                  />
                                </div>
                                <div>
                                  <span
                                    style={{
                                      width: `${item.realized_fraction * 100}%`,
                                    }}
                                    className={`realized-bar mixture-color-${i % 3}`}
                                  />
                                </div>
                              </div>
                              <div className="mixture-numbers">
                                <strong>
                                  {number(item.realized_records)} records
                                </strong>
                                <span>
                                  {(item.requested_fraction * 100).toFixed(1)}%
                                  requested ·{" "}
                                  {(item.realized_fraction * 100).toFixed(1)}%
                                  realized
                                </span>
                              </div>
                            </div>
                          ))}
                          {typeof summary.shortage_records === "number" &&
                            summary.shortage_records > 0 && (
                              <div className="shortage-note">
                                <CircleHelp size={15} />
                                <p>
                                  <strong>
                                    {number(summary.shortage_records)} fewer
                                    records than requested.
                                  </strong>{" "}
                                  The available eligible records could not fill
                                  this budget. The engine kept the result free
                                  of repeated samples.
                                </p>
                              </div>
                            )}
                        </section>
                      )}
                      <div className="panel inspector-panel">
                        <div className="inspector-top">
                          <div
                            className="tabs"
                            role="tablist"
                            aria-label="Result inspector"
                          >
                            <button
                              role="tab"
                              aria-selected={resultTab === "records"}
                              className={
                                resultTab === "records" ? "active" : ""
                              }
                              onClick={() => switchResultTab("records")}
                            >
                              Curated records
                            </button>
                            <button
                              role="tab"
                              aria-selected={resultTab === "decisions"}
                              className={
                                resultTab === "decisions" ? "active" : ""
                              }
                              onClick={() => switchResultTab("decisions")}
                            >
                              Decision explorer
                            </button>
                            <button
                              role="tab"
                              aria-selected={resultTab === "manifest"}
                              className={
                                resultTab === "manifest" ? "active" : ""
                              }
                              onClick={() => switchResultTab("manifest")}
                            >
                              Manifest
                            </button>
                          </div>
                          {resultTab === "decisions" && (
                            <select
                              aria-label="Filter decisions by action"
                              value={action}
                              onChange={(e) => {
                                setAction(e.target.value);
                                setOffset(0);
                              }}
                            >
                              <option value="">All decisions</option>
                              <option value="reject">Rejected</option>
                              <option value="keep">Kept</option>
                              <option value="generate">Generated</option>
                              <option value="select">Selected</option>
                            </select>
                          )}
                        </div>
                        {resultsLoading ? (
                          <div className="loading-block">
                            <LoaderCircle className="spin" size={18} /> Loading
                            evidence…
                          </div>
                        ) : resultTab === "manifest" ? (
                          <pre className="json-preview">
                            {JSON.stringify(run.manifest, null, 2)}
                          </pre>
                        ) : resultTab === "records" ? (
                          <div className="table-scroll">
                            <table className="data-table">
                              <thead>
                                <tr>
                                  <th>Record / source</th>
                                  <th>Content preview</th>
                                  <th>Quality</th>
                                  <th>Relevance</th>
                                  <th />
                                </tr>
                              </thead>
                              <tbody>
                                {records.map((r) => (
                                  <tr key={r.id}>
                                    <td>
                                      <code>{short(r.id)}</code>
                                      <small>
                                        {datasets.find(
                                          (d) => d.id === r.dataset_id,
                                        )?.name ||
                                          r.dataset_id ||
                                          "Derived record"}
                                      </small>
                                    </td>
                                    <td className="content-cell">
                                      <span className="kind-tag">{r.kind}</span>
                                      {r.text.slice(0, 125)}
                                    </td>
                                    <td>{score(r.quality_score)}</td>
                                    <td>{score(r.relevance_score)}</td>
                                    <td>
                                      <button
                                        className="text-button"
                                        onClick={() => setRecord(r)}
                                        aria-label={`Inspect record ${r.id}`}
                                      >
                                        Inspect <ArrowUpRight size={14} />
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            {records.length === 0 && (
                              <div className="empty-state small">
                                <FolderOpen size={28} />
                                <h3>No records selected</h3>
                                <p>
                                  Inspect the decisions and adjust your recipe
                                  before retrying.
                                </p>
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="decisions-list">
                            {decisions.map((d, i) => (
                              <div
                                className="decision-row"
                                key={`${d.record_id}-${d.stage}-${i}`}
                              >
                                <span
                                  className={`decision-action action-${d.action}`}
                                >
                                  {d.action}
                                </span>
                                <div>
                                  <strong>{readable(d.reason)}</strong>
                                  <p>
                                    <code>{short(d.record_id)}</code>
                                    <span>
                                      · {readable(d.stage)} ·{" "}
                                      {datasets.find(
                                        (ds) => ds.id === d.dataset_id,
                                      )?.name || short(d.dataset_id)}
                                    </span>
                                  </p>
                                  {d.details &&
                                    Object.keys(d.details).length > 0 && (
                                      <details>
                                        <summary>Decision details</summary>
                                        <pre>
                                          {JSON.stringify(d.details, null, 2)}
                                        </pre>
                                      </details>
                                    )}
                                </div>
                              </div>
                            ))}
                            {decisions.length === 0 && (
                              <p className="empty-small">
                                No decisions match this filter.
                              </p>
                            )}
                          </div>
                        )}
                        {resultTab !== "manifest" && (
                          <div className="pagination">
                            <span>
                              {total
                                ? `${offset + 1}–${Math.min(offset + 25, total)} of ${number(total)}`
                                : "0 records"}
                            </span>
                            <div>
                              <button
                                className="icon-button"
                                aria-label="Previous results page"
                                disabled={offset === 0 || resultsLoading}
                                onClick={() =>
                                  setOffset(Math.max(0, offset - 25))
                                }
                              >
                                <ChevronLeft size={17} />
                              </button>
                              <button
                                className="icon-button"
                                aria-label="Next results page"
                                disabled={
                                  offset + 25 >= total || resultsLoading
                                }
                                onClick={() => setOffset(offset + 25)}
                              >
                                <ChevronRight size={17} />
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    </>
                  )}
                </section>
              ) : (
                <>
                  <div className="section-heading">
                    <div className="section-title">
                      <h2>Your curation workspace</h2>
                      <span className="count-badge">
                        {selected.length} inputs
                      </span>
                    </div>
                    <div className="section-actions">
                      {run && (
                        <button
                          className="text-button"
                          onClick={() => setShowResults(true)}
                        >
                          <Activity size={15} />{" "}
                          {working ? "View active run" : "View last run"}
                        </button>
                      )}
                      <button
                        className="text-button"
                        onClick={() => {
                          setError("");
                          setRecipeName(recipe?.name || "My curation recipe");
                          setSaveOpen(true);
                        }}
                        disabled={!recipe}
                      >
                        <Save size={15} /> Save recipe
                      </button>
                    </div>
                  </div>
                  <div className="workspace-grid">
                    <section className="panel inputs-panel">
                      <div className="panel-header">
                        <div>
                          <h3>Dataset inputs</h3>
                          <p>Each role has a different purpose.</p>
                        </div>
                        <button
                          className="icon-button tinted"
                          aria-label="Import dataset"
                          onClick={() => setUploadOpen(true)}
                          disabled={!!busy || !connected}
                        >
                          <Plus size={18} />
                        </button>
                      </div>
                      {datasets.length === 0 ? (
                        <div className="empty-state dataset-empty">
                          <div className="empty-illustration">
                            <div />
                            <FileJson2 size={33} />
                            <span>+</span>
                          </div>
                          <h3>Start with your data.</h3>
                          <p>
                            Bring local text or code. We’ll keep the originals
                            and make every change inspectable.
                          </p>
                          <button
                            className="button secondary"
                            onClick={() => setUploadOpen(true)}
                            disabled={!connected}
                          >
                            <Plus size={16} /> Import your first dataset
                          </button>
                          <span className="file-formats">
                            JSONL · JSON · CSV · TXT · code
                          </span>
                        </div>
                      ) : (
                        <div className="dataset-list">
                          {(["source", "target", "holdout"] as Role[]).map(
                            (role) => (
                              <div key={role} className="dataset-group">
                                <div className="dataset-group-label">
                                  <span className={`role-dot ${role}`} />
                                  {role === "target"
                                    ? "Calibration targets"
                                    : role === "holdout"
                                      ? "Evaluation holdouts"
                                      : "Source datasets"}
                                  <span>
                                    {
                                      datasets.filter((d) => d.role === role)
                                        .length
                                    }
                                  </span>
                                </div>
                                {datasets
                                  .filter((d) => d.role === role)
                                  .map((d) => (
                                    <div
                                      className={`dataset-row ${selected.includes(d.id) ? "" : "unselected"}`}
                                      key={d.id}
                                    >
                                      <input
                                        type="checkbox"
                                        checked={selected.includes(d.id)}
                                        onChange={() => toggleDataset(d.id)}
                                        aria-label={`Include ${d.name}`}
                                      />
                                      <div className={`dataset-file ${role}`}>
                                        <FileText size={19} />
                                      </div>
                                      <button
                                        className="dataset-name"
                                        onClick={() => void inspectDataset(d)}
                                        disabled={!!busy}
                                      >
                                        <strong>{d.name}</strong>
                                        <small>
                                          {number(d.record_count)} records{" "}
                                          <span>·</span>{" "}
                                          {roleDescriptions[role]}
                                        </small>
                                      </button>
                                      <ArrowUpRight
                                        size={14}
                                        className="muted"
                                      />
                                    </div>
                                  ))}
                                {datasets.every((d) => d.role !== role) && (
                                  <div className="empty-role">
                                    {role === "source"
                                      ? "Import candidate training data."
                                      : role === "target"
                                        ? "Optional: add examples of your target domain."
                                        : "Optional: add evaluation text to exclude."}
                                  </div>
                                )}
                              </div>
                            ),
                          )}
                        </div>
                      )}
                      <div className="inputs-footer">
                        <LockKeyhole size={14} />
                        <span>
                          Originals are immutable. Your files never leave this
                          machine.
                        </span>
                      </div>
                    </section>
                    {recipe && (
                      <section className="panel recipe-panel">
                        <div className="panel-header">
                          <div>
                            <div className="recipe-kicker">
                              RECIPE / STAGE 0{stage + 1}
                            </div>
                            <h3>{stages[stage].name} your dataset</h3>
                          </div>
                          <div
                            className={`stage-icon small-icon ${stages[stage].color}`}
                          >
                            {(() => {
                              const Icon = stages[stage].icon;
                              return <Icon size={19} />;
                            })()}
                          </div>
                        </div>
                        <div className="recipe-content">
                          {stage === 0 && (
                            <>
                              <p className="panel-description">
                                Check text, reduce duplication, and exclude
                                evaluation overlap. Code preserves case and
                                indentation.
                              </p>
                              <div className="field-grid">
                                <NumberField
                                  label="Minimum characters"
                                  value={recipe.clean.min_chars}
                                  min={0}
                                  onChange={(v) =>
                                    changeRecipe("clean", {
                                      ...recipe.clean,
                                      min_chars: v,
                                    })
                                  }
                                />
                                <NumberField
                                  label="Maximum characters"
                                  value={recipe.clean.max_chars}
                                  min={1}
                                  onChange={(v) =>
                                    changeRecipe("clean", {
                                      ...recipe.clean,
                                      max_chars: v,
                                    })
                                  }
                                />
                              </div>
                              <NumberField
                                label="Near-duplicate similarity"
                                value={recipe.clean.dedup_threshold}
                                min={0}
                                max={1}
                                step={0.01}
                                help="Jaccard threshold from 0 to 1. Higher values keep more near-matches."
                                onChange={(v) =>
                                  changeRecipe("clean", {
                                    ...recipe.clean,
                                    dedup_threshold: v,
                                  })
                                }
                              />
                              <div className="field-grid">
                                <NumberField
                                  label="Holdout n-gram size"
                                  value={recipe.clean.holdout_ngram}
                                  min={1}
                                  help="Contiguous evaluation tokens"
                                  onChange={(v) =>
                                    changeRecipe("clean", {
                                      ...recipe.clean,
                                      holdout_ngram: v,
                                    })
                                  }
                                />
                                <NumberField
                                  label="Holdout overlap threshold"
                                  value={recipe.clean.holdout_overlap}
                                  min={0}
                                  max={1}
                                  step={0.01}
                                  help="Maximum permitted overlap"
                                  onChange={(v) =>
                                    changeRecipe("clean", {
                                      ...recipe.clean,
                                      holdout_overlap: v,
                                    })
                                  }
                                />
                              </div>
                              <div className="info-note">
                                <ShieldCheck size={17} />
                                <p>
                                  Evaluation holdouts are used only for
                                  exclusion. They never inform calibration or
                                  generation prompts.
                                </p>
                              </div>
                            </>
                          )}
                          {stage === 1 && (
                            <>
                              <p className="panel-description">
                                Score transparent quality signals and task
                                relevance using your task description and
                                calibration targets.
                              </p>
                              <label className="field">
                                <span>Task description</span>
                                <textarea
                                  value={recipe.calibrate.task}
                                  onChange={(e) =>
                                    changeRecipe("calibrate", {
                                      ...recipe.calibrate,
                                      task: e.target.value,
                                    })
                                  }
                                  placeholder="e.g. Clear, practical explanations of Python and data engineering"
                                  rows={4}
                                />
                                <small>
                                  Describe the data you want. Holdout text is
                                  never used here.
                                </small>
                              </label>
                              <div className="field-grid">
                                <NumberField
                                  label="Minimum quality score"
                                  value={recipe.calibrate.quality_min}
                                  min={0}
                                  max={1}
                                  step={0.01}
                                  onChange={(v) =>
                                    changeRecipe("calibrate", {
                                      ...recipe.calibrate,
                                      quality_min: v,
                                    })
                                  }
                                />
                                <NumberField
                                  label="Minimum relevance score"
                                  value={recipe.calibrate.relevance_min}
                                  min={0}
                                  max={1}
                                  step={0.01}
                                  onChange={(v) =>
                                    changeRecipe("calibrate", {
                                      ...recipe.calibrate,
                                      relevance_min: v,
                                    })
                                  }
                                />
                              </div>
                              <div className="info-note">
                                <SlidersHorizontal size={17} />
                                <p>
                                  Scores are inspectable heuristics, not model
                                  performance measurements. Unicode text and
                                  code use domain-aware signals.
                                </p>
                              </div>
                            </>
                          )}
                          {stage === 2 && (
                            <>
                              <div className="toggle-field">
                                <div>
                                  <strong>Source-grounded rephrasing</strong>
                                  <p>
                                    Use your explicitly configured local model.
                                  </p>
                                </div>
                                <button
                                  role="switch"
                                  aria-checked={recipe.create.enabled}
                                  aria-label="Enable synthetic generation"
                                  className={`switch ${recipe.create.enabled ? "on" : ""}`}
                                  onClick={() =>
                                    changeRecipe("create", {
                                      ...recipe.create,
                                      enabled: !recipe.create.enabled,
                                    })
                                  }
                                >
                                  <span />
                                </button>
                              </div>
                              {!recipe.create.enabled && (
                                <div className="skip-note">
                                  <Sparkles size={19} />
                                  <div>
                                    <strong>Generation is disabled</strong>
                                    <p>
                                      This stage will be visibly skipped. No
                                      model calls or synthetic records will be
                                      created.
                                    </p>
                                  </div>
                                </div>
                              )}
                              <fieldset
                                disabled={!recipe.create.enabled}
                                className="generation-fields"
                              >
                                <label className="field">
                                  <span>Local model endpoint</span>
                                  <input
                                    type="url"
                                    value={recipe.create.endpoint}
                                    readOnly
                                  />
                                  <small>
                                    Administrator configured on server startup
                                    with --model-endpoint. Loopback HTTP only.
                                  </small>
                                </label>
                                <label className="field">
                                  <span>Model name</span>
                                  <input
                                    value={recipe.create.model}
                                    onChange={(e) =>
                                      changeRecipe("create", {
                                        ...recipe.create,
                                        model: e.target.value,
                                      })
                                    }
                                    placeholder="Name of a model already running locally"
                                  />
                                </label>
                                <div className="field-grid">
                                  <NumberField
                                    label="Maximum synthetic records"
                                    min={1}
                                    value={recipe.create.max_records}
                                    onChange={(v) =>
                                      changeRecipe("create", {
                                        ...recipe.create,
                                        max_records: v,
                                      })
                                    }
                                  />
                                  <NumberField
                                    label="Temperature"
                                    min={0}
                                    max={2}
                                    step={0.1}
                                    value={recipe.create.temperature}
                                    onChange={(v) =>
                                      changeRecipe("create", {
                                        ...recipe.create,
                                        temperature: v,
                                      })
                                    }
                                  />
                                </div>
                              </fieldset>
                              <div className="info-note">
                                <GitBranch size={17} />
                                <p>
                                  Generated records retain parent and model
                                  lineage, then pass exclusion checks again.
                                  Saved outputs support replay; regeneration may
                                  differ.
                                </p>
                              </div>
                            </>
                          )}
                          {stage === 3 && (
                            <>
                              <p className="panel-description">
                                Set a record budget and relative source weights.
                                Seeded selection is deterministic and never
                                samples with replacement.
                              </p>
                              <div className="field-grid">
                                <NumberField
                                  label="Record budget"
                                  value={recipe.compose.max_records}
                                  min={1}
                                  help="Upper bound; shortages are reported."
                                  onChange={(v) =>
                                    changeRecipe("compose", {
                                      ...recipe.compose,
                                      max_records: v,
                                    })
                                  }
                                />
                                <NumberField
                                  label="Random seed"
                                  value={recipe.seed}
                                  help="Same inputs + recipe = same selection."
                                  onChange={(v) => changeRecipe("seed", v)}
                                />
                              </div>
                              <div className="weight-heading">
                                <strong>Source weights</strong>
                                <span>Relative proportions</span>
                              </div>
                              {sources.length === 0 ? (
                                <p className="empty-small">
                                  Select source datasets to configure their
                                  weights.
                                </p>
                              ) : (
                                sources.map((d) => (
                                  <label className="weight-row" key={d.id}>
                                    <span>
                                      <span className="role-dot source" />
                                      {d.name}
                                    </span>
                                    <input
                                      aria-label={`Weight for ${d.name}`}
                                      type="number"
                                      min={0}
                                      step={0.1}
                                      value={
                                        Number.isNaN(
                                          recipe.compose.weights[d.id],
                                        )
                                          ? ""
                                          : (recipe.compose.weights[d.id] ?? 1)
                                      }
                                      onChange={(e) =>
                                        changeRecipe("compose", {
                                          ...recipe.compose,
                                          weights: {
                                            ...recipe.compose.weights,
                                            [d.id]:
                                              e.target.value === ""
                                                ? NaN
                                                : Number(e.target.value),
                                          },
                                        })
                                      }
                                    />
                                  </label>
                                ))
                              )}
                              <div className="info-note">
                                <Layers3 size={17} />
                                <p>
                                  Budgets count records. Token counts are
                                  estimates. The manifest compares requested
                                  weights with the mixture actually produced.
                                </p>
                              </div>
                            </>
                          )}
                        </div>
                        <div className="recipe-footer">
                          <span>
                            <span className="local-dot" /> Runs locally
                          </span>
                          <button
                            className="text-button"
                            onClick={() =>
                              stage < 3 ? setStage(stage + 1) : void startRun()
                            }
                            disabled={!!busy || working}
                          >
                            {stage < 3
                              ? `Next: ${stages[stage + 1].name}`
                              : "Run this recipe"}{" "}
                            <ArrowRight size={16} />
                          </button>
                        </div>
                      </section>
                    )}
                  </div>
                  <div className="bottom-grid">
                    <section className="demo-card">
                      <div className="demo-icon">
                        <FlaskConical size={25} />
                      </div>
                      <div>
                        <span className="eyebrow">
                          A SMALL DATASET. THE WHOLE WORKFLOW.
                        </span>
                        <h3>Take the studio for a spin.</h3>
                        <p>
                          Original fixtures with duplicates, noisy text, code,
                          and evaluation overlap.
                        </p>
                      </div>
                      <button
                        className="button secondary"
                        onClick={() => void loadDemo()}
                        disabled={!!busy || !connected}
                      >
                        {busy === "demo" ? (
                          <LoaderCircle className="spin" size={16} />
                        ) : (
                          <WandSparkles size={16} />
                        )}{" "}
                        Load demo data
                      </button>
                    </section>
                    <section className="saved-card">
                      <div>
                        <Save size={18} />
                        <h3>Reusable recipes</h3>
                        <span>{saved.length}</span>
                      </div>
                      <select
                        aria-label="Load saved recipe"
                        defaultValue=""
                        onChange={(e) => {
                          const match = saved.find(
                            (s) => s.id === e.target.value,
                          );
                          if (match) {
                            setRecipe(match.recipe);
                            setNotice(
                              `Loaded recipe “${match.name}”. Review source weights for this workspace.`,
                            );
                          }
                          e.target.value = "";
                        }}
                      >
                        <option value="">
                          {saved.length
                            ? "Choose a saved recipe…"
                            : "Save your first recipe above"}
                        </option>
                        {saved.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </section>
                  </div>
                </>
              )}
            </>
          )}
          {!loading && page === "datasets" && (
            <section className="panel dataset-library">
              <div className="panel-header">
                <div>
                  <h3>
                    Dataset library{" "}
                    <span className="count-badge">{datasets.length}</span>
                  </h3>
                  <p>Inputs remain unchanged across curation runs.</p>
                </div>
                <label className="search-box">
                  <Search size={16} />
                  <input
                    aria-label="Search datasets"
                    placeholder="Search datasets…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
              </div>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Role</th>
                      <th>Records</th>
                      <th>Input fingerprint</th>
                      <th>Imported</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDatasets.map((d) => (
                      <tr key={d.id}>
                        <td>
                          <button
                            className="library-name"
                            onClick={() => void inspectDataset(d)}
                          >
                            <FileText size={20} />
                            <strong>{d.name}</strong>
                          </button>
                        </td>
                        <td>
                          <span className={`role-badge ${d.role}`}>
                            {d.role}
                          </span>
                        </td>
                        <td>{number(d.record_count)}</td>
                        <td>
                          <code title={d.sha256}>{short(d.sha256)}</code>
                        </td>
                        <td>{date(d.created_at)}</td>
                        <td>
                          <button
                            className="text-button"
                            onClick={() => void inspectDataset(d)}
                          >
                            Inspect <ArrowUpRight size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {filteredDatasets.length === 0 && (
                  <div className="empty-state">
                    <Database size={32} />
                    <h3>
                      {datasets.length
                        ? "No matching datasets"
                        : "Your library starts here"}
                    </h3>
                    <p>
                      {datasets.length
                        ? "Try another search."
                        : "Import local files or load the included demo to explore."}
                    </p>
                    {!datasets.length && (
                      <button
                        className="button secondary"
                        onClick={() => void loadDemo()}
                        disabled={!!busy}
                      >
                        Load demo data
                      </button>
                    )}
                  </div>
                )}
              </div>
            </section>
          )}
          {!loading && page === "runs" && (
            <section className="panel">
              <div className="panel-header">
                <div>
                  <h3>
                    Run history{" "}
                    <span className="count-badge">{runs.length}</span>
                  </h3>
                  <p>
                    Persisted locally, including cancelled and failed attempts.
                  </p>
                </div>
                <button
                  className="text-button"
                  onClick={() => void perform("refresh", refresh)}
                  disabled={!!busy}
                >
                  <RefreshCw size={15} /> Refresh
                </button>
              </div>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Run / recipe</th>
                      <th>Status</th>
                      <th>Inputs</th>
                      <th>Output records</th>
                      <th>Started</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <strong>{r.recipe?.name || "Curation recipe"}</strong>
                          <small>
                            <code>{short(r.id)}</code> · Seed {r.recipe?.seed}
                          </small>
                        </td>
                        <td>
                          <span className={`status status-${r.status}`}>
                            {r.status}
                          </span>
                        </td>
                        <td>{r.dataset_ids?.length ?? "—"}</td>
                        <td>{number(r.summary?.output_records)}</td>
                        <td>{date(r.created_at)}</td>
                        <td>
                          <button
                            className="text-button"
                            onClick={() => void openRun(r)}
                            disabled={!!busy}
                          >
                            Open run <ArrowUpRight size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {runs.length === 0 && (
                  <div className="empty-state">
                    <History size={32} />
                    <h3>A clean slate.</h3>
                    <p>
                      Your recipes, decisions, and export manifests will appear
                      here after your first run.
                    </p>
                    <button
                      className="button secondary"
                      onClick={() => setPage("studio")}
                    >
                      Open curation studio <ArrowRight size={15} />
                    </button>
                  </div>
                )}
              </div>
            </section>
          )}
          <footer className="page-footer">
            <span>
              <LockKeyhole size={13} /> Local by default. Transparent by design.
            </span>
            <span>
              Original open-source curation tools{" "}
              <span className="footer-dot">·</span> No training-performance
              claims
            </span>
          </footer>
        </main>
      </div>
      {uploadOpen && (
        <UploadDialog
          onClose={closeUpload}
          onImported={async (d) => {
            await refresh();
            setSelected((current) => [...new Set([...current, d.id])]);
            setNotice(
              `Imported “${d.name}” with ${number(d.record_count)} records.`,
            );
          }}
        />
      )}
      {inspecting && (
        <Dialog title={inspecting.name} onClose={closeInspector} wide>
          <div className="inspect-meta">
            <span className={`role-badge ${inspecting.role}`}>
              {inspecting.role}
            </span>
            <span>{number(inspecting.record_count)} records</span>
            <span>{roleDescriptions[inspecting.role]}</span>
          </div>
          <div className="hash-line">
            <span>SHA-256</span>
            <code>{inspecting.sha256}</code>
          </div>
          <div className="sample-list">
            {inspecting.records?.map((r, i) => (
              <article className="sample-record" key={r.id}>
                <div>
                  <strong>Record {i + 1}</strong>
                  <span className="kind-tag">{r.kind}</span>
                  <code>{short(r.id)}</code>
                </div>
                <pre>{r.text}</pre>
                {r.metadata && Object.keys(r.metadata).length > 0 && (
                  <details>
                    <summary>Metadata and declared license</summary>
                    <pre>{JSON.stringify(r.metadata, null, 2)}</pre>
                  </details>
                )}
              </article>
            ))}
          </div>
          <div className="dialog-footer">
            <p>
              Showing up to 25 original records. Imported content is displayed
              as text.
            </p>
            <button className="button secondary" onClick={closeInspector}>
              Done
            </button>
          </div>
        </Dialog>
      )}
      {record && (
        <Dialog title="Record inspector" onClose={closeRecord} wide>
          <div className="inspect-meta">
            <span className="kind-tag">{record.kind}</span>
            <code>{record.id}</code>
            <span>
              Quality {score(record.quality_score)} · Relevance{" "}
              {score(record.relevance_score)}
            </span>
          </div>
          <pre className="record-full">{record.text}</pre>
          <div className="lineage-block">
            <h3>
              <GitBranch size={17} /> Provenance & lineage
            </h3>
            <pre>
              {JSON.stringify(
                {
                  dataset_id: record.dataset_id,
                  source_record_id: record.source_record_id,
                  lineage: record.lineage,
                  metadata: record.metadata,
                },
                null,
                2,
              )}
            </pre>
          </div>
          <div className="dialog-footer">
            <p>Original and synthetic records carry explicit provenance.</p>
            <button className="button secondary" onClick={closeRecord}>
              Done
            </button>
          </div>
        </Dialog>
      )}
      {saveOpen && (
        <Dialog title="Save reusable recipe" onClose={closeSave}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void perform("save", async () => {
                if (!recipeName.trim())
                  throw new Error("Give this recipe a name.");
                const next = { ...recipe!, name: recipeName.trim() };
                await post("/recipes", { name: next.name, recipe: next });
                setRecipe(next);
                await refresh();
                setSaveOpen(false);
                setNotice(`Saved recipe “${next.name}”.`);
              });
            }}
          >
            <div className="dialog-body">
              {error && (
                <div className="alert error compact" role="alert">
                  <CircleHelp size={17} />
                  <p>{error}</p>
                </div>
              )}
              <p className="panel-description">
                Keep these settings for another run. Dataset weights refer to
                the source IDs in this workspace.
              </p>
              <label className="field">
                <span>Recipe name</span>
                <input
                  autoFocus
                  required
                  maxLength={120}
                  value={recipeName}
                  onChange={(e) => setRecipeName(e.target.value)}
                />
              </label>
            </div>
            <div className="dialog-footer">
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  recipe && downloadJson(recipe, "curation-recipe.json")
                }
              >
                <ArrowDownToLine size={15} /> Download JSON
              </button>
              <button
                type="submit"
                className="button primary"
                disabled={!!busy}
              >
                <Save size={16} /> Save recipe
              </button>
            </div>
          </form>
        </Dialog>
      )}
      {helpOpen && (
        <Dialog title="A local studio with a paper trail" onClose={closeHelp}>
          <div className="dialog-body help-content">
            <p>
              Data Curator is an original open-source tool for building small,
              inspectable text and code datasets on your own machine.
            </p>
            <h3>One workflow, four stages</h3>
            {stages.map((s, i) => (
              <div className="help-stage" key={s.name}>
                <span className={`mini-stage ${s.color}`}>{i + 1}</span>
                <div>
                  <strong>{s.name}</strong>
                  <p>{s.description}</p>
                </div>
              </div>
            ))}
            <h3>Three distinct input roles</h3>
            <p>
              <strong>Sources</strong> provide candidate output records.{" "}
              <strong>Targets</strong> guide task relevance.{" "}
              <strong>Holdouts</strong> are evaluation examples used only to
              detect and exclude overlap.
            </p>
            <h3>Honest limits</h3>
            <p>
              Processing is in memory and intended for local experiments.
              Uploads are capped at 20 MiB, 10,000 records per file, and 200,000
              characters per record. Quality is heuristic and token counts are
              estimates. No training improvements or proprietary product parity
              are claimed.
            </p>
            <p>
              Optional generation calls only an explicitly configured loopback
              model. No model is downloaded and no imported code is executed.
            </p>
          </div>
          <div className="dialog-footer">
            <span className="muted">MIT-licensed original code</span>
            <button className="button primary" onClick={closeHelp}>
              Back to the studio
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  detail,
  icon,
  highlight = false,
}: {
  label: string;
  value: string;
  detail: string;
  icon: ReactNode;
  highlight?: boolean;
}) {
  return (
    <div className={`metric-card ${highlight ? "highlight" : ""}`}>
      <div>
        <span>{label}</span>
        {icon}
      </div>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
function score(value: unknown) {
  return typeof value === "number" ? value.toFixed(3) : "—";
}

function UploadDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (d: Dataset) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("source");
  const [kind, setKind] = useState("text");
  const [textField, setTextField] = useState("text");
  const [license, setLicense] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  function choose(file: File | null) {
    if (!file) return;
    setError("");
    setFile(null);
    if (file.size > 20 * 1024 * 1024) {
      setError(
        "This file exceeds the 20 MiB upload limit. Split it into smaller datasets first.",
      );
      return;
    }
    if (file.size === 0) {
      setError("This file is empty. Choose a file with at least one record.");
      return;
    }
    setFile(file);
    setName(file.name.replace(/\.[^.]+$/, ""));
    setKind(
      /\.(py|js|ts|tsx|jsx|rs|go|java|c|cpp|h|rb|sh|sql|swift)$/i.test(
        file.name,
      )
        ? "code"
        : "text",
    );
  }
  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) {
      setError("Choose a local file to import.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const buffer = await file.arrayBuffer();
      let content: string;
      try {
        content = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
      } catch {
        throw new Error(
          "This file is not valid UTF-8. Save it as UTF-8 and try again.",
        );
      }
      const result = await post<Dataset>("/datasets", {
        name: name.trim(),
        role,
        filename: file.name,
        content,
        text_field: textField,
        kind,
        ...(license.trim() ? { license: license.trim() } : {}),
      });
      await onImported(result);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title="Import a local dataset" onClose={onClose}>
      <form onSubmit={(e) => void upload(e)}>
        <div className="dialog-body">
          {error && (
            <div className="alert error compact" role="alert">
              <CircleHelp size={17} />
              <p>{error}</p>
            </div>
          )}
          <label
            className={`drop-zone ${dragging ? "dragging" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              choose(e.dataTransfer.files[0] || null);
            }}
          >
            <input
              type="file"
              aria-label="Dataset file"
              onChange={(e) => choose(e.target.files?.[0] || null)}
              disabled={busy}
            />
            <div className="upload-icon">
              {file ? <FileJson2 size={27} /> : <ArrowDownToLine size={27} />}
            </div>
            <strong>{file ? file.name : "Drop a file here, or browse"}</strong>
            <span>
              {file
                ? `${(file.size / 1024).toFixed(1)} KiB · click to replace`
                : "JSONL, JSON, CSV, TXT, Markdown, and code · up to 20 MiB"}
            </span>
          </label>
          <label className="field">
            <span>Dataset name</span>
            <input
              required
              value={name}
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Engineering knowledge base"
            />
          </label>
          <fieldset className="role-selector">
            <legend>Dataset role</legend>
            {(["source", "target", "holdout"] as Role[]).map((r) => (
              <label key={r} className={role === r ? "chosen" : ""}>
                <input
                  type="radio"
                  name="dataset-role"
                  value={r}
                  checked={role === r}
                  onChange={() => setRole(r)}
                />
                <strong>
                  {r === "target"
                    ? "Target"
                    : r === "holdout"
                      ? "Holdout"
                      : "Source"}
                </strong>
                <small>{roleDescriptions[r]}</small>
              </label>
            ))}
          </fieldset>
          <div className="field-grid">
            <label className="field">
              <span>Content kind</span>
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="text">Text</option>
                <option value="code">Code (preserve formatting)</option>
              </select>
            </label>
            <label className="field">
              <span>Text column / field</span>
              <input
                required
                value={textField}
                onChange={(e) => setTextField(e.target.value)}
              />
              <small>For JSON, JSONL, and CSV.</small>
            </label>
          </div>
          <label className="field">
            <span>
              Declared license{" "}
              <small className="inline-optional">optional</small>
            </span>
            <input
              value={license}
              onChange={(e) => setLicense(e.target.value)}
              placeholder="e.g. CC0-1.0 or your usage terms"
            />
            <small>
              Recorded as provenance; you are responsible for rights to your
              inputs.
            </small>
          </label>
          <div className="info-note">
            <LockKeyhole size={16} />
            <p>
              Imported content is treated as untrusted text. Code is never
              executed. Invalid imports are rejected atomically.
            </p>
          </div>
        </div>
        <div className="dialog-footer">
          <button
            type="button"
            className="button secondary"
            onClick={onClose}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || !file}
          >
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Plus size={16} />
            )}
            {busy ? "Importing…" : "Import dataset"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
