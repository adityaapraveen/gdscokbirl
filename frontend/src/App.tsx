import { useState } from "react";
import {
  Activity,
  ArrowUpRight,
  ChartNoAxesCombined,
  Database,
  Eye,
  EyeOff,
  History,
  LayoutDashboard,
  Wind,
} from "lucide-react";
import {
  AccuracyPage,
  EvolutionPage,
  OverviewPage,
  QualityPage,
} from "./pages";
import { dayLabel } from "./common";

const navigation = [
  {
    id: "overview",
    label: "Overview",
    icon: LayoutDashboard,
    kicker: "DECISION COCKPIT",
    title: "Tomorrow’s wind, at decision time.",
    description: "See the forecast your procurement team could actually use.",
  },
  {
    id: "accuracy",
    label: "Accuracy",
    icon: ChartNoAxesCombined,
    kicker: "HISTORICAL RELIABILITY",
    title: "Understand the size of the miss.",
    description:
      "Evaluate the forecasts available when the procurement decision was made.",
  },
  {
    id: "evolution",
    label: "Forecast Evolution",
    icon: History,
    kicker: "DECISION REPLAY",
    title: "A forecast is a moving target.",
    description:
      "Trace each publication. Separate what was known from what came later.",
  },
  {
    id: "quality",
    label: "Data Quality",
    icon: Database,
    kicker: "DATA HEALTH",
    title: "Trust starts with the source.",
    description:
      "Inspect coverage, timestamps and the records behind every calculation.",
  },
] as const;
type Page = (typeof navigation)[number]["id"];

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [delivery, setDelivery] = useState("2026-01-22");
  const [cutoff, setCutoff] = useState("09:20");
  const [hour, setHour] = useState("18");
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const replayKey = `${delivery}|${cutoff}|${hour}`;
  const reveal = revealedKey === replayKey;
  const current = navigation.find((item) => item.id === page)!;
  const decisionPage = page === "overview" || page === "evolution";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to analysis
      </a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            setPage("overview");
          }}
        >
          <span className="brand-symbol">
            <Wind size={25} />
          </span>
          <span>
            WindLens<small>PROCUREMENT INTELLIGENCE</small>
          </span>
        </a>
        <div className="workspace-label">
          WORKSPACE <span>PILOT</span>
        </div>
        <nav aria-label="Main navigation">
          {navigation.map((item) => (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              className={page === item.id ? "active" : ""}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={18} />
              {item.label}
              {page === item.id && <i />}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <div className="sidebar-note-icon">
            <Activity size={18} />
          </div>
          <strong>Every decision has a timestamp.</strong>
          <p>
            Explore the information available before tomorrow’s power had to be
            bought.
          </p>
          <span>JANUARY 2026 PILOT</span>
        </div>
        <div className="sidebar-bottom">
          <div className="source-dot" />
          <div>
            Elexon source snapshot<small>GB operationally metered wind</small>
          </div>
          <a
            href="https://github.com/adityaapraveen/gdscokbirl"
            target="_blank"
            rel="noreferrer"
            aria-label="Open source repository"
          >
            <ArrowUpRight size={15} />
          </a>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>
            <strong>{current.label}</strong>
          </div>
          <div className="topbar-meta">
            <span className="pilot-dot" />
            Historical analysis
            <span className="topbar-separator" />
            JAN 2026
          </div>
        </header>
        <main id="main-content">
          <div className="page-intro">
            <div>
              <div className="eyebrow">{current.kicker}</div>
              <h1>{current.title}</h1>
              <p>{current.description}</p>
            </div>
            <span className="edition">
              GB WIND
              <br />
              <strong>Day-ahead</strong>
            </span>
          </div>
          <section className="controls" aria-label="Analysis controls">
            <div className="control-fields">
              {decisionPage && (
                <label className="field">
                  <span>Delivery date</span>
                  <input
                    type="date"
                    min="2026-01-02"
                    max="2026-02-01"
                    value={delivery}
                    onInput={(event) => {
                      if (event.currentTarget.value) {
                        setDelivery(event.currentTarget.value);
                        setRevealedKey(null);
                      }
                    }}
                  />
                </label>
              )}
              <label className="field">
                <span title="Publications after this London time on D-1 are excluded.">
                  Decision cutoff · D-1
                </span>
                <input
                  type="time"
                  value={cutoff}
                  onInput={(event) => {
                    if (event.currentTarget.value) {
                      setCutoff(event.currentTarget.value);
                      setRevealedKey(null);
                    }
                  }}
                />
              </label>
              <div className="control-context">
                <span>{decisionPage ? "Delivery day" : "Decision dates"}</span>
                <strong>
                  {decisionPage ? dayLabel(delivery) : "1 – 31 January 2026"}
                </strong>
                <small>Europe/London · GMT</small>
              </div>
            </div>
            {decisionPage ? (
              <button
                className={`button reveal-button ${reveal ? "revealed" : ""}`}
                onClick={() => setRevealedKey(reveal ? null : replayKey)}
              >
                {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
                <span>{reveal ? "Reset replay" : "Reveal actual outcome"}</span>
              </button>
            ) : (
              <span className="small-badge">
                {cutoff} D-1 · NATIVE TIMESTAMPS
              </span>
            )}
          </section>
          {page === "overview" && (
            <OverviewPage
              delivery={delivery}
              cutoff={cutoff}
              reveal={reveal}
              onEvolution={(value) => {
                setHour(value);
                setRevealedKey(null);
                setPage("evolution");
              }}
            />
          )}
          {page === "evolution" && (
            <EvolutionPage
              delivery={delivery}
              cutoff={cutoff}
              reveal={reveal}
              hour={hour}
              setHour={(value) => {
                setHour(value);
                setRevealedKey(null);
              }}
            />
          )}
          {page === "accuracy" && <AccuracyPage cutoff={cutoff} />}
          {page === "quality" && <QualityPage cutoff={cutoff} />}
          <footer className="footer">
            <span>
              WindLens <i /> Day-Ahead Wind Procurement Intelligence
            </span>
            <span>MW measures power. MWh measures energy.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
