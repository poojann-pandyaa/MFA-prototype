import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { CheckCircle2, ShieldAlert, XCircle } from 'lucide-react';
import api, { errText, rupees } from '../api';
import { useApp } from '../ctx';
import PinPad from '../components/PinPad';
import { STEP_COMPONENTS } from '../components/steps';

export default function Pay() {
  const { id } = useParams();
  const { refresh, setXrayId } = useApp();
  const [p, setP] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);

  const load = useCallback(async () => {
    try { setP((await api.get(`/pay/${id}`)).data); } catch (e) { setError(errText(e)); }
  }, [id]);

  useEffect(() => { setXrayId(id); load(); }, [id, load, setXrayId]);

  const cancel = async () => { try { await api.post(`/pay/${id}/cancel`); await load(); } catch (e) { setError(errText(e)); } };
  const pay = async (pin) => {
    setBusy(true); setError('');
    try { const r = (await api.post(`/pay/${id}/complete`, { pin })).data; setDone(r); await refresh(); await load(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };

  if (!p) return error ? <div role="alert" className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div> : <p className="text-sm text-slate-500">Loading...</p>;

  const steps = p.plan?.steps || [];
  const Step = p.status === 'pending' ? STEP_COMPONENTS[steps[p.current_step]?.method] : null;
  const summary = (
    <div className="rounded-xl bg-slate-50 border p-3 text-sm mb-4">
      <div className="text-2xl font-semibold">{rupees(p.amount)}</div>
      <div className="mt-1">to <strong>{p.registered_name || p.payee_vpa}</strong></div>
      <div className="text-xs text-slate-500">{p.payee_vpa} &middot; {p.channel === 'collect' ? 'collect request' : p.channel === 'qr' ? 'scanned QR' : 'payment'}</div>
    </div>
  );

  return (
    <div>
      {summary}
      {error && <div role="alert" className="p-3 mb-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}

      {p.status === 'pending' && (
        <>
          <div className="text-xs text-slate-600 mb-3">
            Safety check: step <strong>{(p.current_step ?? 0) + 1}</strong> of <strong>{steps.length}</strong>
            {p.plan.time_seconds > 0 && <> &middot; about {Math.round(p.plan.time_seconds)} seconds</>}
            <div className="mt-1 flex gap-1">{steps.map((s, i) => <span key={s.index} className={`h-1.5 flex-1 rounded ${i < p.steps_done ? 'bg-green-500' : i === p.current_step ? 'bg-[#1b7f8c]' : 'bg-slate-200'}`} title={s.name} />)}</div>
          </div>
          {Step ? <Step key={`${p.id}-${p.current_step}`} paymentId={p.id} index={p.current_step} onResult={() => load()} onCancel={cancel} /> : <p className="text-sm">Unknown step.</p>}
          {steps[p.current_step]?.tier !== 'slow' && <button onClick={cancel} className="mt-3 w-full text-sm text-slate-500 underline">Cancel this payment</button>}
        </>
      )}

      {p.status === 'approved' && (
        <>
          {steps.length > 0 && <div className="mb-3 text-sm flex items-center gap-2 text-green-700"><CheckCircle2 size={16} /> Safety checks complete.</div>}
          <PinPad onSubmit={pay} busy={busy} />
          <button onClick={cancel} className="mt-3 w-full text-sm text-slate-500 underline">Cancel this payment</button>
        </>
      )}

      {p.status === 'completed' && (
        <div className="text-center space-y-3 py-4" data-testid="success">
          <CheckCircle2 className="mx-auto text-green-600" size={48} />
          <h2 className="text-lg font-semibold">Payment sent</h2>
          <p className="text-sm text-slate-600">{rupees(p.amount)} sent to {p.registered_name || p.payee_vpa}.{done && <> New balance {rupees(done.balance)}.</>}</p>
          <Link to="/" className="inline-block px-4 py-2 rounded-lg bg-[#14325c] text-white text-sm">Back to home</Link>
        </div>
      )}

      {p.status === 'blocked' && (
        <div className="text-center space-y-3 py-4" data-testid="blocked">
          <ShieldAlert className="mx-auto text-red-600" size={48} />
          <h2 className="text-lg font-semibold">Payment stopped</h2>
          <p className="text-sm text-slate-700">{p.message}</p>
          <Link to="/" className="inline-block px-4 py-2 rounded-lg bg-[#14325c] text-white text-sm">Back to home</Link>
        </div>
      )}

      {p.status === 'cancelled' && (
        <div className="text-center space-y-3 py-4">
          <XCircle className="mx-auto text-slate-500" size={48} />
          <h2 className="text-lg font-semibold">Payment cancelled</h2>
          <Link to="/" className="inline-block px-4 py-2 rounded-lg bg-[#14325c] text-white text-sm">Back to home</Link>
        </div>
      )}
    </div>
  );
}
