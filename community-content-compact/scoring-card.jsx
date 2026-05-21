/**
 * Community Content Compact — Scoring Card
 *
 * Standalone React component. Two tabs: Score (form) and Card (nutrition-panel display).
 * No external dependencies beyond React.
 *
 * Named exports:
 *   CaribooSignalsCard  — pre-filled Track A assessment for Cariboo Signals (15/20 Sound)
 *
 * Default export:
 *   ScoringCard({ title, producer, community, date, track, scores, disqualified, disqualifierNote })
 *
 * Usage:
 *   import ScoringCard, { CaribooSignalsCard } from './scoring-card.jsx'
 *
 *   // Pre-filled:
 *   <CaribooSignalsCard />
 *
 *   // Interactive:
 *   <ScoringCard title="My Project" track="A" />
 */

import React, { useState } from 'react';

// ---------------------------------------------------------------------------
// Track A — 6 categories, 20 points
// ---------------------------------------------------------------------------

const TRACK_A_CATEGORIES = [
  { key: 'transparency',    label: 'I. Transparency',           max: 4 },
  { key: 'sourceIntegrity', label: 'II. Source Integrity',      max: 3 },
  { key: 'displacement',    label: 'III. Displacement',         max: 2 },
  { key: 'consent',         label: 'IV. Consent & Attribution', max: 4 },
  { key: 'benefitFlow',     label: 'V. Benefit Flow',           max: 5 },
  { key: 'accountability',  label: 'VI. Accountability',        max: 2 },
];

const TRACK_A_VERDICTS = [
  { min: 17, max: 20, label: 'Exemplary',    bg: '#14532d', fg: '#fff' },
  { min: 12, max: 16, label: 'Sound',        bg: '#1e40af', fg: '#fff' },
  { min: 6,  max: 11, label: 'Developing',   bg: '#854d0e', fg: '#fff' },
  { min: 0,  max: 5,  label: 'Needs Work',   bg: '#7f1d1d', fg: '#fff' },
];

// ---------------------------------------------------------------------------
// Track B — 7 categories, 35 points
// ---------------------------------------------------------------------------

const TRACK_B_CATEGORIES = [
  { key: 'transparency',    label: 'I. Transparency',            max: 7 },
  { key: 'sourceIntegrity', label: 'II. Source Integrity',       max: 4 },
  { key: 'displacement',    label: 'III. Displacement',          max: 5 },
  { key: 'consent',         label: 'IV. Consent & Attribution',  max: 4 },
  { key: 'benefitFlow',     label: 'V. Benefit Flow',            max: 6 },
  { key: 'accountability',  label: 'VI. Accountability',         max: 4 },
  { key: 'governance',      label: 'VII. Governance Durability', max: 5 },
];

const TRACK_B_VERDICTS = [
  { min: 32, max: 35, label: 'Community-Serving', bg: '#14532d', fg: '#fff' },
  { min: 26, max: 31, label: 'Defensible',        bg: '#15803d', fg: '#fff' },
  { min: 19, max: 25, label: 'Conditional',       bg: '#854d0e', fg: '#fff' },
  { min: 11, max: 18, label: 'High Risk',         bg: '#9a3412', fg: '#fff' },
  { min: 0,  max: 10, label: 'Extractive',        bg: '#7f1d1d', fg: '#fff' },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getCategories(track) {
  return track === 'A' ? TRACK_A_CATEGORIES : TRACK_B_CATEGORIES;
}

function getVerdicts(track) {
  return track === 'A' ? TRACK_A_VERDICTS : TRACK_B_VERDICTS;
}

function getMaxTotal(track) {
  return getCategories(track).reduce((s, c) => s + c.max, 0);
}

function getVerdict(total, track, disqualified) {
  if (disqualified) return { label: 'Disqualified', bg: '#111827', fg: '#fff' };
  const verdicts = getVerdicts(track);
  return verdicts.find(v => total >= v.min && total <= v.max) ?? verdicts[verdicts.length - 1];
}

function barColor(pct) {
  if (pct >= 0.7) return '#16a34a';
  if (pct >= 0.4) return '#ca8a04';
  return '#dc2626';
}

// ---------------------------------------------------------------------------
// Card view (nutrition-panel style)
// ---------------------------------------------------------------------------

function NutritionCard({ title, producer, community, date, track, scores, disqualified, disqualifierNote }) {
  const categories = getCategories(track);
  const verdicts   = getVerdicts(track);
  const maxTotal   = getMaxTotal(track);
  const total      = categories.reduce((s, c) => s + (Number(scores[c.key]) || 0), 0);
  const verdict    = getVerdict(total, track, disqualified);

  const s = {
    card: {
      fontFamily: "'Arial Narrow', Arial, Helvetica, sans-serif",
      border: '3px solid #111',
      width: 300,
      padding: '8px 10px 10px',
      background: '#fff',
      color: '#111',
    },
    headerLabel: { fontSize: 9, letterSpacing: 2, textTransform: 'uppercase', color: '#555', marginBottom: 2 },
    title:       { fontSize: 30, fontWeight: 900, lineHeight: 1, letterSpacing: -1 },
    ruleThick:   { borderTop: '8px solid #111', margin: '6px 0' },
    ruleMedium:  { borderTop: '4px solid #111', margin: '6px 0' },
    ruleThin:    { borderTop: '1px solid #ccc', margin: '5px 0' },
    metaRow:     { display: 'flex', justifyContent: 'space-between', fontSize: 10, lineHeight: 1.6 },
    totalRow:    { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' },
    totalLabel:  { fontSize: 13, fontWeight: 700 },
    totalValue:  { fontSize: 30, fontWeight: 900, lineHeight: 1 },
    badge: (v)  => ({ background: v.bg, color: v.fg, fontWeight: 700, fontSize: 13, textAlign: 'center', padding: '3px 0', marginTop: 5, letterSpacing: 0.5, textTransform: 'uppercase' }),
    catRow:      { display: 'flex', justifyContent: 'space-between', fontSize: 10, fontWeight: 600, marginBottom: 2 },
    barTrack:    { height: 5, background: '#e5e7eb', borderRadius: 2, marginBottom: 5 },
    keyHeader:   { fontSize: 9, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 3 },
    keyRow:      { display: 'flex', justifyContent: 'space-between', fontSize: 9, lineHeight: 1.7 },
    footer:      { fontSize: 8, color: '#888', marginTop: 5 },
  };

  return (
    <div style={s.card}>
      <div style={s.headerLabel}>Community Content Compact · Track {track}</div>
      <div style={s.title}>Content<br />Facts</div>
      <div style={s.ruleThick} />

      {[['Content', title], producer && ['Producer', producer], community && ['Community', community], date && ['Reviewed', date]]
        .filter(Boolean)
        .map(([label, value]) => (
          <div key={label} style={s.metaRow}>
            <span style={{ fontWeight: 700 }}>{label}</span>
            <span style={{ maxWidth: 185, textAlign: 'right' }}>{value}</span>
          </div>
        ))}

      <div style={s.ruleMedium} />

      <div style={s.totalRow}>
        <span style={s.totalLabel}>Total Score</span>
        <span style={s.totalValue}>{disqualified ? 'DQ' : `${total} / ${maxTotal}`}</span>
      </div>
      <div style={s.badge(verdict)}>{verdict.label}</div>

      <div style={s.ruleMedium} />

      {disqualified ? (
        <div style={{ fontSize: 10, lineHeight: 1.5 }}>
          <strong>Disqualifier triggered:</strong><br />{disqualifierNote || 'See evaluation notes.'}
        </div>
      ) : (
        categories.map((cat, i) => {
          const score = Number(scores[cat.key]) || 0;
          const pct   = score / cat.max;
          return (
            <React.Fragment key={cat.key}>
              <div style={s.catRow}>
                <span>{cat.label}</span>
                <span style={{ fontWeight: 400 }}>{score} / {cat.max}</span>
              </div>
              <div style={{ ...s.barTrack, marginBottom: i < categories.length - 1 ? 5 : 0 }}>
                <div style={{ width: `${pct * 100}%`, height: '100%', background: barColor(pct), borderRadius: 2 }} />
              </div>
            </React.Fragment>
          );
        })
      )}

      <div style={s.ruleMedium} />

      <div style={s.keyHeader}>Verdict Key · Track {track}</div>
      {verdicts.map(v => (
        <div key={v.label} style={s.keyRow}>
          <span style={{ color: v.bg, fontWeight: 600 }}>{v.label}</span>
          <span>{v.min}–{v.max}</span>
        </div>
      ))}
      <div style={s.keyRow}><span style={{ fontWeight: 600 }}>Disqualified</span><span>Any DQ</span></div>

      <div style={s.ruleThin} />
      <div style={s.footer}>Community Content Compact · github.com/zirnhelt/curated-podcast-generator · CC BY 4.0</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Score form view
// ---------------------------------------------------------------------------

function ScoreForm({ fields, setFields, track, setTrack, scores, setScores }) {
  const categories = getCategories(track);

  function updateScore(key, val) {
    const cat = categories.find(c => c.key === key);
    const num = Math.max(0, Math.min(cat.max, Number(val) || 0));
    setScores(prev => ({ ...prev, [key]: num }));
  }

  const inputStyle = {
    border: '1px solid #ccc', borderRadius: 4, padding: '4px 6px',
    fontSize: 13, width: '100%', boxSizing: 'border-box',
  };
  const labelStyle = { fontSize: 12, fontWeight: 600, display: 'block', marginBottom: 3 };
  const rowStyle   = { marginBottom: 12 };

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', fontSize: 13 }}>
      <div style={rowStyle}>
        <label style={labelStyle}>Content title</label>
        <input style={inputStyle} value={fields.title} onChange={e => setFields(f => ({ ...f, title: e.target.value }))} />
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>Producer</label>
        <input style={inputStyle} value={fields.producer} onChange={e => setFields(f => ({ ...f, producer: e.target.value }))} />
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>Community</label>
        <input style={inputStyle} value={fields.community} onChange={e => setFields(f => ({ ...f, community: e.target.value }))} />
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>Date reviewed</label>
        <input style={inputStyle} value={fields.date} onChange={e => setFields(f => ({ ...f, date: e.target.value }))} />
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>Track</label>
        <select style={inputStyle} value={track} onChange={e => { setTrack(e.target.value); setScores({}); }}>
          <option value="A">Track A — Community-Insider (/ 20)</option>
          <option value="B">Track B — External Producer (/ 35)</option>
        </select>
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>
          <input type="checkbox" checked={fields.disqualified} onChange={e => setFields(f => ({ ...f, disqualified: e.target.checked }))} />
          {' '}Disqualifier triggered (DQ1 or DQ2)
        </label>
        {fields.disqualified && (
          <input style={{ ...inputStyle, marginTop: 4 }} placeholder="Note which disqualifier and why" value={fields.disqualifierNote} onChange={e => setFields(f => ({ ...f, disqualifierNote: e.target.value }))} />
        )}
      </div>

      {!fields.disqualified && (
        <>
          <div style={{ borderTop: '2px solid #111', paddingTop: 10, marginBottom: 10, fontWeight: 700, fontSize: 13 }}>Category Scores</div>
          {categories.map(cat => (
            <div key={cat.key} style={{ ...rowStyle, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <label style={{ ...labelStyle, marginBottom: 0, flex: 1 }}>{cat.label}</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <input
                  type="number" min={0} max={cat.max}
                  style={{ ...inputStyle, width: 52 }}
                  value={scores[cat.key] ?? ''}
                  onChange={e => updateScore(cat.key, e.target.value)}
                />
                <span style={{ color: '#888', fontSize: 11 }}>/ {cat.max}</span>
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Default export — full tabbed component
// ---------------------------------------------------------------------------

export default function ScoringCard({
  title: initTitle       = '',
  producer: initProducer = '',
  community: initCom     = '',
  date: initDate         = '',
  track: initTrack       = 'A',
  scores: initScores     = {},
  disqualified: initDQ   = false,
  disqualifierNote: initNote = '',
}) {
  const [tab, setTab]           = useState('card');
  const [track, setTrack]       = useState(initTrack);
  const [scores, setScores]     = useState(initScores);
  const [fields, setFields]     = useState({
    title: initTitle, producer: initProducer, community: initCom,
    date: initDate, disqualified: initDQ, disqualifierNote: initNote,
  });

  const tabBtn = (id, label) => ({
    onClick: () => setTab(id),
    style: {
      padding: '6px 16px', border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 13,
      borderBottom: tab === id ? '3px solid #111' : '3px solid transparent',
      background: 'none', color: tab === id ? '#111' : '#777',
    },
  });

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', maxWidth: 340 }}>
      <div style={{ display: 'flex', borderBottom: '1px solid #ddd', marginBottom: 16 }}>
        <button {...tabBtn('score', 'Score')}>Score</button>
        <button {...tabBtn('card', 'Card')}>Card</button>
      </div>

      {tab === 'score' ? (
        <ScoreForm fields={fields} setFields={setFields} track={track} setTrack={setTrack} scores={scores} setScores={setScores} />
      ) : (
        <NutritionCard
          title={fields.title}
          producer={fields.producer}
          community={fields.community}
          date={fields.date}
          track={track}
          scores={scores}
          disqualified={fields.disqualified}
          disqualifierNote={fields.disqualifierNote}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pre-filled export — Cariboo Signals Track A, 15/20 Sound
// ---------------------------------------------------------------------------

export function CaribooSignalsCard() {
  return (
    <NutritionCard
      title="Cariboo Signals"
      producer="Erich Zirnhelt"
      community="Cariboo region, BC"
      date="May 2026"
      track="A"
      scores={{
        transparency:    3,
        sourceIntegrity: 3,
        displacement:    2,
        consent:         2,
        benefitFlow:     4,
        accountability:  1,
      }}
    />
  );
}
