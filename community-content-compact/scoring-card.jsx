/**
 * Community Content Compact — Scoring Card
 *
 * A standalone React component rendered in a nutrition-panel style.
 * Drop into any React project. No external dependencies beyond React.
 *
 * Usage:
 *   <ScoringCard
 *     title="Cariboo Signals"
 *     producer="Erich Zirnhelt"
 *     community="Cariboo region, BC"
 *     date="May 2026"
 *     track="B"
 *     scores={{
 *       transparency: 3,
 *       sourceIntegrity: 1,
 *       displacement: 0,
 *       consent: 2,
 *       benefitFlow: 1,
 *       accountability: 1,
 *       governance: 0,
 *     }}
 *   />
 */

import React from 'react';

const VERDICTS = [
  { min: 32, max: 35, label: 'Community-Serving', bg: '#14532d', fg: '#ffffff' },
  { min: 26, max: 31, label: 'Defensible',        bg: '#15803d', fg: '#ffffff' },
  { min: 19, max: 25, label: 'Conditional',       bg: '#854d0e', fg: '#ffffff' },
  { min: 11, max: 18, label: 'High Risk',         bg: '#9a3412', fg: '#ffffff' },
  { min: 0,  max: 10, label: 'Extractive',        bg: '#7f1d1d', fg: '#ffffff' },
];

const CATEGORIES = [
  { key: 'transparency',    label: 'I. Transparency',           max: 7 },
  { key: 'sourceIntegrity', label: 'II. Source Integrity',      max: 4 },
  { key: 'displacement',    label: 'III. Displacement / Mandate', max: 5 },
  { key: 'consent',         label: 'IV. Consent & Attribution', max: 4 },
  { key: 'benefitFlow',     label: 'V. Benefit Flow',           max: 6 },
  { key: 'accountability',  label: 'VI. Accountability',        max: 4 },
  { key: 'governance',      label: 'VII. Governance Durability', max: 5 },
];

const MAX_TOTAL = CATEGORIES.reduce((sum, c) => sum + c.max, 0); // 35

function getVerdict(total, disqualified) {
  if (disqualified) return { label: 'Disqualified', bg: '#111827', fg: '#ffffff' };
  return VERDICTS.find(v => total >= v.min && total <= v.max) ?? VERDICTS[VERDICTS.length - 1];
}

function barColor(pct) {
  if (pct >= 0.7) return '#16a34a';
  if (pct >= 0.4) return '#ca8a04';
  return '#dc2626';
}

const styles = {
  card: {
    fontFamily: "'Arial Narrow', Arial, Helvetica, sans-serif",
    border: '3px solid #111',
    width: 300,
    padding: '8px 10px 10px',
    backgroundColor: '#ffffff',
    color: '#111111',
    boxSizing: 'border-box',
  },
  headerLabel: {
    fontSize: 9,
    letterSpacing: 2,
    textTransform: 'uppercase',
    color: '#555',
    marginBottom: 2,
  },
  title: {
    fontSize: 30,
    fontWeight: 900,
    lineHeight: 1,
    letterSpacing: -1,
  },
  subtitle: {
    fontSize: 10,
    color: '#555',
    marginTop: 2,
  },
  rule8: { borderTop: '8px solid #111', margin: '6px 0' },
  rule4: { borderTop: '4px solid #111', margin: '6px 0' },
  rule1: { borderTop: '1px solid #111', margin: '5px 0' },
  ruleThin: { borderTop: '1px solid #ccc', margin: '5px 0' },
  metaRow: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 10,
    lineHeight: 1.6,
  },
  metaLabel: { fontWeight: 700 },
  totalRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'baseline',
  },
  totalLabel: { fontSize: 13, fontWeight: 700 },
  totalValue: { fontSize: 30, fontWeight: 900, lineHeight: 1 },
  verdictBadge: (verdict) => ({
    backgroundColor: verdict.bg,
    color: verdict.fg,
    fontWeight: 700,
    fontSize: 13,
    textAlign: 'center',
    padding: '3px 0',
    marginTop: 5,
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  }),
  catLabel: { fontSize: 10, fontWeight: 600 },
  catScore: { fontSize: 10 },
  barTrack: {
    height: 5,
    backgroundColor: '#e5e7eb',
    borderRadius: 2,
    marginTop: 2,
  },
  verdictKeyHeader: { fontSize: 9, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 3 },
  verdictKeyRow: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 9,
    lineHeight: 1.7,
  },
  footer: { fontSize: 8, color: '#888', marginTop: 5 },
};

export function CaribooSignalsCard() {
  return (
    <ScoringCard
      title="Cariboo Signals"
      producer="Erich Zirnhelt"
      community="Cariboo region, BC"
      date="May 2026"
      track="B"
      scores={{
        transparency:    3,
        sourceIntegrity: 1,
        displacement:    0,
        consent:         2,
        benefitFlow:     1,
        accountability:  1,
        governance:      0,
      }}
    />
  );
}

export default function ScoringCard({
  title = 'Untitled Content',
  producer = '',
  community = '',
  date = '',
  track = 'B',
  scores = {},
  disqualified = false,
  disqualifierNote = '',
}) {
  const total = CATEGORIES.reduce((sum, cat) => sum + (Number(scores[cat.key]) || 0), 0);
  const verdict = getVerdict(total, disqualified);

  return (
    <div style={styles.card}>

      {/* Header */}
      <div style={styles.headerLabel}>Community Content Compact · Track {track}</div>
      <div style={styles.title}>Content<br />Facts</div>
      <div style={styles.rule8} />

      {/* Metadata */}
      {[
        ['Content', title],
        producer   ? ['Producer',  producer]   : null,
        community  ? ['Community', community]  : null,
        date       ? ['Reviewed',  date]       : null,
      ].filter(Boolean).map(([label, value]) => (
        <div key={label} style={styles.metaRow}>
          <span style={styles.metaLabel}>{label}</span>
          <span style={{ maxWidth: 190, textAlign: 'right' }}>{value}</span>
        </div>
      ))}

      <div style={styles.rule4} />

      {/* Total */}
      <div style={styles.totalRow}>
        <span style={styles.totalLabel}>Total Score</span>
        <span style={styles.totalValue}>
          {disqualified ? 'DQ' : `${total} / ${MAX_TOTAL}`}
        </span>
      </div>
      <div style={styles.verdictBadge(verdict)}>{verdict.label}</div>

      <div style={styles.rule4} />

      {/* Category breakdown */}
      {disqualified ? (
        <div style={{ fontSize: 10, lineHeight: 1.5 }}>
          <strong>Disqualifier triggered:</strong><br />
          {disqualifierNote || 'See evaluation notes.'}
        </div>
      ) : (
        CATEGORIES.map((cat, i) => {
          const score = Number(scores[cat.key]) || 0;
          const pct   = score / cat.max;
          return (
            <React.Fragment key={cat.key}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={styles.catLabel}>{cat.label}</span>
                <span style={styles.catScore}>{score} / {cat.max}</span>
              </div>
              <div style={styles.barTrack}>
                <div style={{
                  width: `${pct * 100}%`,
                  height: '100%',
                  backgroundColor: barColor(pct),
                  borderRadius: 2,
                }} />
              </div>
              {i < CATEGORIES.length - 1 && <div style={{ marginBottom: 4 }} />}
            </React.Fragment>
          );
        })
      )}

      <div style={styles.rule4} />

      {/* Verdict key */}
      <div style={styles.verdictKeyHeader}>Verdict Key</div>
      {VERDICTS.map(v => (
        <div key={v.label} style={styles.verdictKeyRow}>
          <span style={{ color: v.bg, fontWeight: 600 }}>{v.label}</span>
          <span>{v.min}–{v.max}</span>
        </div>
      ))}
      <div style={{ ...styles.verdictKeyRow, color: '#111', fontWeight: 600 }}>
        <span>Disqualified</span>
        <span>Any DQ</span>
      </div>

      <div style={styles.ruleThin} />
      <div style={styles.footer}>
        Community Content Compact · github.com/zirnhelt/curated-podcast-generator · CC BY 4.0
      </div>

    </div>
  );
}
