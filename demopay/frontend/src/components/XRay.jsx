import { Fragment, useEffect, useState } from 'react';
import api, { errText } from '../api';
import { useApp } from '../ctx';

const SEV = { LOW: 'bg-green-100 text-green-800', MEDIUM: 'bg-amber-100 text-amber-800', HIGH: 'bg-orange-200 text-orange-900', CRITICAL: 'bg-red-200 text-red-900' };
const HEADS = [['takeover', 'Takeover (someone else paying)'], ['coached', 'Coached (owner being tricked)'], ['payee', 'Bad payee']];
const MAX = 12;

function Bar({ label, value, thresholds }) {
  return (
    <div className="mb-3">
      <div className="flex justify-between text-xs mb-1"><span>{label}</span><span className="tabular-nums">{value?.toFixed(1)}</span></div>
      <div className="relative h-3 rounded bg-slate-100">
        <div className="absolute inset-y-0 left-0 rounded bg-[#1b7f8c]" style={{ width: `${Math.min(100, ((value || 0) / MAX) * 100)}%` }} />
        {thresholds && ['MEDIUM', 'HIGH', 'CRITICAL'].map((k) => (
          <div key={k} className="absolute inset-y-[-2px] w-px bg-slate-700" style={{ left: `${(thresholds[k] / MAX) * 100}%` }} title={`${k} at ${thresholds[k]}`} />
        ))}
      </div>
    </div>
  );
}

export default function XRay() {
  const { xrayId } = useApp();
  const [x, setX] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setX(null); setError('');
    if (!xrayId) return;
    api.get(`/demo/xray/${xrayId}`).then((r) => setX(r.data)).catch((e) => setError(errText(e)));
  }, [xrayId]);

  return (
    <aside className="rounded-3xl bg-white shadow-xl border border-slate-200 p-5" aria-label="Model X-ray">
      <h2 className="text-lg font-semibold text-[#14325c]">Model X-ray</h2>
      <p className="text-xs text-amber-800 bg-amber-50 rounded-lg p-2 mt-1 mb-4">Demo only. Shows what the model saw and why. A real app never shows this to the user. All data is simulated.</p>
      {!xrayId && <p className="text-sm text-slate-500">Make a payment to see how the model scores it.</p>}
      {error && <div role="alert" className="text-sm text-red-700">{error}</div>}
      {x && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`px-2.5 py-1 rounded-full text-sm font-medium ${SEV[x.severity]}`}>{x.severity}</span>
            <span className="text-sm">threat: <strong>{x.threat}</strong></span>
            <span className="text-xs text-slate-500">model: {x.model}</span>
          </div>

          <section>
            <h3 className="text-sm font-medium mb-2">Three detectors (higher = rarer among genuine payments)</h3>
            {HEADS.map(([k, label]) => <Bar key={k} label={label} value={x.surprises?.[k]} thresholds={x.thresholds} />)}
            <p className="text-xs text-slate-500">Vertical lines mark the MEDIUM, HIGH and CRITICAL levels, set so only a small share of genuine payments reach each.</p>
          </section>

          <section>
            <h3 className="text-sm font-medium mb-1">Decision engine</h3>
            <p className="text-sm">Chance this is a scam: <strong>{(x.plan.p_scam * 100).toFixed(3)}%</strong> <span className="text-xs text-slate-500">(base rate 1 in {Math.round(1 / x.plan.base_rate).toLocaleString('en-IN')})</span></p>
            <p className="text-sm">Decision: <strong>{x.plan.decision}</strong> &middot; expected cost Rs {x.plan.expected_cost}</p>
            {x.plan.steps.length > 0 && (
              <ol className="mt-2 space-y-1.5">
                {x.plan.steps.map((s) => (
                  <li key={s.index} className="text-sm border rounded-lg p-2">
                    <div className="flex justify-between"><strong>{s.index + 1}. {s.name}</strong><span className="text-xs text-slate-500">{s.tier} &middot; ~{s.est_seconds >= 60 ? `${Math.round(s.est_seconds / 60)} min` : `${s.est_seconds}s`}</span></div>
                    <div className="text-xs text-slate-600">{s.why}</div>
                    <div className="text-xs">Doubt after this step: {(s.doubt_after * 100).toFixed(3)}%</div>
                  </li>
                ))}
              </ol>
            )}
            {x.plan.notes.map((n) => <p key={n} className="text-xs text-slate-500 mt-1">{n}</p>)}
          </section>

          {x.reasons?.length > 0 && (
            <section>
              <h3 className="text-sm font-medium mb-1">What pushed the score up</h3>
              <ul className="text-sm list-disc pl-5">{x.reasons.map((r) => <li key={r.input}>{r.input.replace(/_/g, ' ')} (+{r.pushes_up_by})</li>)}</ul>
            </section>
          )}

          <section>
            <h3 className="text-sm font-medium mb-1">Inputs sent to the model</h3>
            <dl className="text-xs grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
              {Object.entries(x.inputs).map(([k, v]) => (<Fragment key={k}><dt className="text-slate-500">{k.replace(/_/g, ' ')}</dt><dd>{String(v)}</dd></Fragment>))}
            </dl>
          </section>
        </div>
      )}
    </aside>
  );
}
