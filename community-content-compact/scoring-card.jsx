import { useState, useMemo } from "react";

const TRACKS = {
  A: {
    label: "Track A",
    sublabel: "Community-Insider",
    categories: [
      { id: "i",   label: "Transparency",         max: 4 },
      { id: "ii",  label: "Source Integrity",      max: 3 },
      { id: "iii", label: "Displacement",          max: 2 },
      { id: "iv",  label: "Consent & Attribution", max: 4 },
      { id: "v",   label: "Benefit Flow",          max: 5 },
      { id: "vi",  label: "Accountability",        max: 2 },
    ],
    max: 20,
    verdicts: [
      { min: 0,  max: 5,  label: "NEEDS WORK",  color: "#b03020" },
      { min: 6,  max: 11, label: "DEVELOPING",  color: "#c05800" },
      { min: 12, max: 16, label: "SOUND",        color: "#1e6e40" },
      { min: 17, max: 20, label: "EXEMPLARY",    color: "#145530" },
    ],
  },
  B: {
    label: "Track B",
    sublabel: "External Production",
    categories: [
      { id: "i",   label: "Transparency",          max: 7 },
      { id: "ii",  label: "Source Integrity",       max: 4 },
      { id: "iii", label: "Displacement",           max: 5 },
      { id: "iv",  label: "Consent & Attribution",  max: 4 },
      { id: "v",   label: "Benefit Flow",           max: 6 },
      { id: "vi",  label: "Accountability",         max: 4 },
      { id: "vii", label: "Governance Durability",  max: 5 },
    ],
    max: 35,
    verdicts: [
      { min: 0,  max: 10, label: "EXTRACTIVE",         color: "#b03020" },
      { min: 11, max: 18, label: "HIGH RISK",          color: "#c05800" },
      { min: 19, max: 25, label: "CONDITIONAL",        color: "#a08000" },
      { min: 26, max: 31, label: "DEFENSIBLE",         color: "#1e6e40" },
      { min: 32, max: 35, label: "COMMUNITY-SERVING",  color: "#145530" },
    ],
  },
};

export default function App() {
  const [contentName, setContentName] = useState("");
  const [producer, setProducer] = useState("");
  const [track, setTrack] = useState("A");
  const [dq, setDq] = useState(false);
  const [scores, setScores] = useState({});
  const [tab, setTab] = useState("score");

  const trackData = TRACKS[track];

  const totalScore = useMemo(
    () => trackData.categories.reduce((s, c) => s + (parseInt(scores[c.id]) || 0), 0),
    [scores, track, trackData]
  );

  const verdict = useMemo(() => {
    if (dq) return { label: "DISQUALIFIED", color: "#7d1a1a" };
    return trackData.verdicts.find((v) => totalScore >= v.min && totalScore <= v.max);
  }, [dq, totalScore, trackData]);

  const pct = Math.round((totalScore / trackData.max) * 100);

  const setScore = (id, val) => {
    const cat = trackData.categories.find((c) => c.id === id);
    const n = parseInt(val);
    const clamped = isNaN(n) ? "" : Math.max(0, Math.min(cat.max, n));
    setScores((p) => ({ ...p, [id]: clamped }));
  };

  const switchTrack = (t) => {
    setTrack(t);
    setScores({});
  };

  return (
    <div style={{ fontFamily: "system-ui, -apple-system, sans-serif", background: "#eeebe6", minHeight: "100vh" }}>
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }

        /* Tabs */
        .tabs { display: flex; background: #1a1a1a; }
        .tab-btn { flex: 1; padding: 13px 0; font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
          text-transform: uppercase; cursor: pointer; background: none; border: none; color: #666;
          border-bottom: 2px solid transparent; transition: color 0.15s, border-color 0.15s; }
        .tab-btn.on { color: #f0ede8; border-bottom-color: #f0ede8; }

        /* Form */
        .form { padding: 16px; max-width: 480px; margin: 0 auto; }
        .f-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #888; margin-bottom: 5px; display: block; }
        .f-field { margin-bottom: 14px; }
        .f-input { width: 100%; padding: 9px 11px; border: 1px solid #d8d4cd; border-radius: 4px;
          font-size: 14px; background: white; color: #1a1a1a; outline: none; }
        .f-input:focus { border-color: #999; }
        .track-row { display: flex; border: 1px solid #d8d4cd; border-radius: 4px; overflow: hidden; background: white; }
        .track-opt { flex: 1; padding: 9px 6px; text-align: center; font-size: 12px; font-weight: 600;
          cursor: pointer; background: none; border: none; color: #999; transition: all 0.15s; line-height: 1.3; }
        .track-opt.on { background: #1a1a1a; color: white; }
        .dq-row { display: flex; align-items: center; justify-content: space-between; padding: 9px 11px;
          background: white; border: 1px solid #d8d4cd; border-radius: 4px; }
        .dq-status { font-size: 13px; color: #444; }
        .toggle { position: relative; width: 42px; height: 24px; display: inline-block; }
        .toggle input { opacity: 0; width: 0; height: 0; }
        .tog-slider { position: absolute; inset: 0; background: #ddd; border-radius: 24px; cursor: pointer; transition: 0.2s; }
        .tog-slider:before { content: ""; position: absolute; width: 18px; height: 18px; left: 3px; top: 3px;
          background: white; border-radius: 50%; transition: 0.2s; }
        input:checked + .tog-slider { background: #b03020; }
        input:checked + .tog-slider:before { transform: translateX(18px); }
        .score-table { background: white; border: 1px solid #d8d4cd; border-radius: 4px; overflow: hidden; }
        .score-row { display: flex; align-items: center; padding: 8px 12px; border-bottom: 1px solid #f0ece6; gap: 8px; }
        .score-row:last-child { border-bottom: none; }
        .score-name { font-size: 13px; color: #333; flex: 1; }
        .score-num { width: 52px; padding: 5px 8px; border: 1px solid #d8d4cd; border-radius: 3px;
          font-size: 14px; text-align: center; background: #fafaf8; color: #1a1a1a; outline: none; }
        .score-num:focus { border-color: #999; }
        .score-of { font-size: 11px; color: #bbb; width: 26px; }
        .go-btn { width: 100%; margin-top: 16px; padding: 12px; background: #1a1a1a; color: white;
          border: none; border-radius: 4px; font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
          text-transform: uppercase; cursor: pointer; }

        /* Panel */
        .panel-wrap { padding: 20px 16px 32px; display: flex; justify-content: center; }
        .panel { width: 290px; background: white; border: 3px solid #1a1a1a; font-family: Arial, Helvetica, sans-serif; }
        .p-hd { padding: 6px 10px 5px; border-bottom: 9px solid #1a1a1a; }
        .p-hd-title { font-size: 28px; font-weight: 900; color: #1a1a1a; line-height: 1; letter-spacing: -0.02em; }
        .p-hd-sub { font-size: 9px; font-weight: 700; color: #666; letter-spacing: 0.03em; margin-top: 2px; }
        .p-meta { padding: 4px 10px; border-bottom: 4px solid #1a1a1a; }
        .p-meta-lbl { font-size: 8px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #999; }
        .p-meta-val { font-size: 13px; font-weight: 700; color: #1a1a1a; line-height: 1.25; margin-top: 1px; }
        .p-meta-sub { font-size: 10px; color: #777; }
        .p-row { display: flex; align-items: center; justify-content: space-between; padding: 3px 10px; border-bottom: 1px solid #e8e4de; }
        .p-row-lbl { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: #999; }
        .p-row-val { font-size: 10px; font-weight: 700; color: #1a1a1a; }
        .p-row-pass { font-size: 10px; font-weight: 700; color: #1e6e40; }
        .p-row-fail { font-size: 10px; font-weight: 700; color: #b03020; }
        .p-divider-thick { height: 4px; background: #1a1a1a; }
        .p-cat-hd { display: flex; justify-content: space-between; background: #1a1a1a; padding: 3px 10px; }
        .p-cat-hd span { font-size: 8px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: white; }
        .p-cat { display: flex; align-items: center; padding: 4px 10px; border-bottom: 1px solid #f0ece6; gap: 6px; }
        .p-cat:last-of-type { border-bottom: none; }
        .p-cat-name { font-size: 10px; color: #1a1a1a; font-weight: 500; flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .p-cat-bar-bg { width: 64px; height: 5px; background: #e8e4de; border-radius: 2px; flex-shrink: 0; overflow: hidden; }
        .p-cat-bar { height: 5px; border-radius: 2px; background: #1a1a1a; }
        .p-cat-sc { font-size: 9px; font-weight: 700; color: #1a1a1a; width: 28px; text-align: right; flex-shrink: 0; }
        .p-total { display: flex; justify-content: space-between; align-items: baseline;
          padding: 5px 10px; border-top: 5px solid #1a1a1a; border-bottom: 1px solid #1a1a1a; }
        .p-total-lbl { font-size: 13px; font-weight: 900; color: #1a1a1a; }
        .p-total-val { font-size: 22px; font-weight: 900; color: #1a1a1a; line-height: 1; }
        .p-pct { padding: 4px 10px 5px; border-bottom: 8px solid #1a1a1a; }
        .p-pct-bg { height: 6px; background: #e8e4de; border-radius: 3px; overflow: hidden; margin-bottom: 2px; }
        .p-pct-bar { height: 6px; border-radius: 3px; transition: width 0.3s; }
        .p-pct-lbl { font-size: 8px; color: #aaa; font-weight: 600; }
        .p-verdict { padding: 10px; text-align: center; }
        .p-verdict-grade { font-size: 26px; font-weight: 900; line-height: 1; letter-spacing: -0.02em; }
        .p-verdict-sub { font-size: 8px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #aaa; margin-top: 3px; }
        .p-dq-block { padding: 14px 10px; text-align: center; border-top: 4px solid #1a1a1a; }
        .p-dq-text { font-size: 22px; font-weight: 900; color: #b03020; }
        .p-dq-sub { font-size: 8px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: #aaa; margin-top: 3px; }
        .p-footer { padding: 4px 10px; border-top: 1px solid #e8e4de; }
        .p-footer-text { font-size: 7px; color: #ccc; line-height: 1.5; }
      `}</style>

      {/* Tabs */}
      <div className="tabs">
        <button className={`tab-btn ${tab === "score" ? "on" : ""}`} onClick={() => setTab("score")}>Score</button>
        <button className={`tab-btn ${tab === "card" ? "on" : ""}`} onClick={() => setTab("card")}>Card</button>
      </div>

      {/* Score tab */}
      {tab === "score" && (
        <div className="form">
          <div className="f-field">
            <label className="f-label">Content</label>
            <input className="f-input" placeholder="Title or description" value={contentName} onChange={(e) => setContentName(e.target.value)} />
          </div>
          <div className="f-field">
            <label className="f-label">Producer</label>
            <input className="f-input" placeholder="Producer or organization" value={producer} onChange={(e) => setProducer(e.target.value)} />
          </div>
          <div className="f-field">
            <label className="f-label">Track</label>
            <div className="track-row">
              <button className={`track-opt ${track === "A" ? "on" : ""}`} onClick={() => switchTrack("A")}>
                Track A<br />Community-Insider
              </button>
              <button className={`track-opt ${track === "B" ? "on" : ""}`} onClick={() => switchTrack("B")}>
                Track B<br />External
              </button>
            </div>
          </div>
          <div className="f-field">
            <label className="f-label">Disqualifier triggered</label>
            <div className="dq-row">
              <span className="dq-status">{dq ? "Yes — evaluation ends here" : "No — proceed to scoring"}</span>
              <label className="toggle">
                <input type="checkbox" checked={dq} onChange={(e) => setDq(e.target.checked)} />
                <span className="tog-slider" />
              </label>
            </div>
          </div>
          {!dq && (
            <div className="f-field">
              <label className="f-label">Category Scores</label>
              <div className="score-table">
                {trackData.categories.map((cat) => (
                  <div key={cat.id} className="score-row">
                    <span className="score-name">{cat.label}</span>
                    <input
                      type="number"
                      className="score-num"
                      min={0}
                      max={cat.max}
                      value={scores[cat.id] ?? ""}
                      placeholder="0"
                      onChange={(e) => setScore(cat.id, e.target.value)}
                    />
                    <span className="score-of">/{cat.max}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <button className="go-btn" onClick={() => setTab("card")}>View Card →</button>
        </div>
      )}

      {/* Card tab */}
      {tab === "card" && (
        <div className="panel-wrap">
          <div className="panel">

            {/* Header */}
            <div className="p-hd">
              <div className="p-hd-title">Content</div>
              <div className="p-hd-title">Accountability</div>
              <div className="p-hd-title">Facts</div>
              <div className="p-hd-sub">The Community Content Compact</div>
            </div>

            {/* Content */}
            <div className="p-meta">
              <div className="p-meta-lbl">Content</div>
              <div className="p-meta-val">{contentName || "—"}</div>
              {producer && <div className="p-meta-sub">{producer}</div>}
            </div>

            {/* Track row */}
            <div className="p-row" style={{ borderBottom: "1px solid #e8e4de" }}>
              <span className="p-row-lbl">Track</span>
              <span className="p-row-val">{trackData.label} — {trackData.sublabel}</span>
            </div>

            {/* DQ row */}
            <div className="p-row" style={{ borderBottom: "none" }}>
              <span className="p-row-lbl">Disqualifiers</span>
              {dq
                ? <span className="p-row-fail">TRIGGERED</span>
                : <span className="p-row-pass">CLEAR</span>
              }
            </div>

            <div className="p-divider-thick" />

            {dq ? (
              <div className="p-dq-block">
                <div className="p-dq-text">DISQUALIFIED</div>
                <div className="p-dq-sub">Fails regardless of score</div>
              </div>
            ) : (
              <>
                <div className="p-cat-hd">
                  <span>Category</span>
                  <span>Score</span>
                </div>

                {trackData.categories.map((cat) => {
                  const s = parseInt(scores[cat.id]) || 0;
                  return (
                    <div key={cat.id} className="p-cat">
                      <span className="p-cat-name">{cat.label}</span>
                      <div className="p-cat-bar-bg">
                        <div className="p-cat-bar" style={{ width: `${(s / cat.max) * 100}%` }} />
                      </div>
                      <span className="p-cat-sc">{s}/{cat.max}</span>
                    </div>
                  );
                })}

                <div className="p-total">
                  <span className="p-total-lbl">Total Score</span>
                  <span className="p-total-val">{totalScore}/{trackData.max}</span>
                </div>

                <div className="p-pct">
                  <div className="p-pct-bg">
                    <div className="p-pct-bar" style={{ width: `${pct}%`, background: verdict?.color || "#1a1a1a" }} />
                  </div>
                  <div className="p-pct-lbl">{pct}% of maximum</div>
                </div>

                <div className="p-verdict">
                  <div className="p-verdict-grade" style={{ color: verdict?.color || "#1a1a1a" }}>
                    {verdict?.label || "—"}
                  </div>
                  <div className="p-verdict-sub">Community Content Compact Rating</div>
                </div>
              </>
            )}

            <div className="p-footer">
              <div className="p-footer-text">
                Cariboo Signals · Community Content Compact · May 2026<br />
                github.com/zirnhelt/curated-podcast-generator
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
