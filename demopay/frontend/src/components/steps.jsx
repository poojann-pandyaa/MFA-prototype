import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Clock, UserCheck } from 'lucide-react';
import api, { errText } from '../api';

// Each step starts its challenge, shows it, sends the answer, and reports the server's reply through onResult.
function useStep(paymentId, index) {
  const [ch, setCh] = useState(null);
  const [error, setError] = useState('');
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api.post(`/pay/${paymentId}/steps/${index}/start`).then((r) => setCh(r.data)).catch((e) => setError(errText(e)));
  }, [paymentId, index]);
  return { ch, error, setError };
}

async function send(paymentId, index, body) {
  return (await api.post(`/pay/${paymentId}/steps/${index}/answer`, body)).data;
}

const Card = ({ icon, title, children }) => (
  <div className="space-y-3">
    <div className="flex items-center gap-2 text-[#14325c]">{icon}<h2 className="font-semibold">{title}</h2></div>
    {children}
  </div>
);
const Err = ({ text }) => (text ? <div role="alert" className="p-2.5 rounded-lg bg-red-50 text-red-700 text-sm">{text}</div> : null);
const Primary = (props) => <button {...props} className="w-full bg-[#1b7f8c] text-white rounded-lg py-2.5 font-medium disabled:opacity-40" />;

export function WarningStep({ paymentId, index, onResult }) {
  const { ch, error, setError } = useStep(paymentId, index);
  const [left, setLeft] = useState(null);
  const [answers, setAnswers] = useState({});
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!ch) return undefined;
    setLeft(Math.ceil(ch.data.min_read_seconds));
    const t = setInterval(() => setLeft((s) => (s > 0 ? s - 1 : 0)), 1000);
    return () => clearInterval(t);
  }, [ch]);

  if (!ch) return <Err text={error} /> || null;
  const d = ch.data;
  const ready = left === 0 && ack && d.questions.every((q) => answers[q.id]);
  const go = async () => {
    setBusy(true); setError('');
    try { onResult(await send(paymentId, index, { nonce: ch.nonce, acknowledged: ack, answers })); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };
  return (
    <Card icon={<AlertTriangle />} title={d.title}>
      <p className="text-sm bg-amber-50 border border-amber-200 rounded-lg p-3">{d.body}</p>
      {d.questions.map((q) => (
        <fieldset key={q.id} className="text-sm">
          <legend className="mb-1">{q.text}</legend>
          {['no', 'yes'].map((v) => (
            <label key={v} className="mr-4"><input type="radio" name={q.id} checked={answers[q.id] === v} onChange={() => setAnswers({ ...answers, [q.id]: v })} /> {v === 'yes' ? 'Yes' : 'No'}</label>
          ))}
        </fieldset>
      ))}
      <label className="text-sm flex items-start gap-2"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" /> I have read this warning.</label>
      <Err text={error} />
      <Primary disabled={!ready || busy} onClick={go}>{left > 0 ? `Please read (${left})` : busy ? 'Checking...' : 'Continue'}</Primary>
      <p className="text-xs text-slate-500">{d.helpline}</p>
    </Card>
  );
}

export function TypedConfirmStep({ paymentId, index, onResult }) {
  const { ch, error, setError } = useStep(paymentId, index);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  if (!ch) return <Err text={error} />;
  const go = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const r = await send(paymentId, index, { nonce: ch.nonce, typed });
      if (r.retry) setError(`${r.message} ${r.attempts_left} attempt(s) left.`);
      onResult(r);
    } catch (err) { setError(errText(err)); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={go}><Card icon={<UserCheck />} title="Confirm who you are paying">
      <p className="text-sm">{ch.data.prompt}</p>
      <p className="text-xs text-slate-500">Payee id: {ch.data.payee_vpa}</p>
      <input value={typed} onChange={(e) => setTyped(e.target.value)} className="w-full border rounded-lg px-3 py-2" placeholder="Registered name" aria-label="Registered name" autoFocus />
      <Err text={error} />
      <Primary disabled={!typed || busy}>Confirm</Primary>
    </Card></form>
  );
}

export function PayeeCheckStep({ paymentId, index, onResult }) {
  const { ch, error, setError } = useStep(paymentId, index);
  const [busy, setBusy] = useState(false);
  if (!ch) return <Err text={error} />;
  const d = ch.data;
  const answer = async (confirmed) => {
    setBusy(true); setError('');
    try { onResult(await send(paymentId, index, { nonce: ch.nonce, confirmed })); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };
  return (
    <Card icon={<UserCheck />} title="Check the payee">
      <div className="rounded-lg border p-3 text-sm space-y-1">
        <div><span className="text-slate-500">Payee id:</span> {d.payee_vpa}</div>
        <div><span className="text-slate-500">Registered name:</span> <strong>{d.registered_name || 'not found'}</strong></div>
        {d.age_days != null && <div><span className="text-slate-500">Account age:</span> {d.age_days < 30 ? `${Math.round(d.age_days)} day(s), very new` : `${Math.round(d.age_days)} days`}</div>}
        {d.similar_to && <div className="text-amber-800">Looks very similar to a payee you may know: {d.similar_to}. Did you mean that one?</div>}
        {d.reported && <div className="text-red-700">This account has been reported before.</div>}
      </div>
      <p className="text-sm font-medium">{d.question}</p>
      <Err text={error} />
      <div className="grid grid-cols-2 gap-2">
        <button disabled={busy} onClick={() => answer(false)} className="border rounded-lg py-2.5">No, stop</button>
        <button disabled={busy} onClick={() => answer(true)} className="bg-[#1b7f8c] text-white rounded-lg py-2.5">Yes, continue</button>
      </div>
    </Card>
  );
}

export function HoldStep({ paymentId, index, onResult, onCancel }) {
  const { ch, error, setError } = useStep(paymentId, index);
  const [left, setLeft] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!ch) return undefined;
    const end = new Date(ch.data.release_at).getTime();
    const tick = () => setLeft(Math.max(0, Math.ceil((end - Date.now()) / 1000)));
    tick();
    const t = setInterval(tick, 500);
    return () => clearInterval(t);
  }, [ch]);
  if (!ch) return <Err text={error} />;
  const d = ch.data;
  const go = async () => {
    setBusy(true); setError('');
    try { onResult(await send(paymentId, index, { nonce: ch.nonce })); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };
  const mm = String(Math.floor((left ?? 0) / 60)).padStart(2, '0');
  const ss = String((left ?? 0) % 60).padStart(2, '0');
  return (
    <Card icon={<Clock />} title="This payment is on a short hold">
      <div className="text-center text-4xl font-semibold tabular-nums" aria-live="polite">{mm}:{ss}</div>
      {d.shortened_for_demo && <p className="text-xs text-amber-800 bg-amber-50 rounded-lg p-2">Demo: the real hold is {Math.round(d.real_hold_seconds / 60)} minutes. It is shortened to {d.hold_seconds} seconds here.</p>}
      <ul className="text-sm list-disc pl-5 space-y-1">{d.tips.map((t) => <li key={t}>{t}</li>)}</ul>
      <Err text={error} />
      <Primary disabled={left > 0 || busy} onClick={go}>{left > 0 ? 'Please wait...' : 'Continue'}</Primary>
      <button onClick={onCancel} className="w-full border rounded-lg py-2.5">Cancel this payment</button>
    </Card>
  );
}

export const STEP_COMPONENTS = { warning: WarningStep, typed_confirm: TypedConfirmStep, payee_check: PayeeCheckStep, cooling_off: HoldStep };
