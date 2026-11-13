import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Send as SendIcon } from 'lucide-react';
import api, { errText, rupees } from '../api';
import { useApp } from '../ctx';

const STATUS_STYLE = {
  completed: 'bg-green-100 text-green-800', approved: 'bg-blue-100 text-blue-800', pending: 'bg-amber-100 text-amber-800',
  blocked: 'bg-red-100 text-red-800', cancelled: 'bg-slate-100 text-slate-700', planned: 'bg-slate-100 text-slate-700',
};

export default function Home() {
  const { me, refresh, setXrayId } = useApp();
  const [rows, setRows] = useState([]);
  const [personas, setPersonas] = useState([]);
  const [persona, setPersona] = useState('asha');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const load = async () => {
    try {
      setRows((await api.get('/pay')).data);
      if (me?.demo) setPersonas((await api.get('/demo/personas')).data);
    } catch (e) { setError(errText(e)); }
  };
  useEffect(() => { setXrayId(null); load(); }, [me?.demo]);  // eslint-disable-line react-hooks/exhaustive-deps

  const loadPersona = async () => {
    setError('');
    try {
      const r = (await api.post('/demo/persona', { name: persona })).data;
      setNote(`${r.persona}: ${r.story} ${r.payments} past payments loaded.`);
      await refresh(); await load();
    } catch (e) { setError(errText(e)); }
  };
  const reset = async () => { await api.post('/demo/reset'); setNote('Demo reset.'); await refresh(); await load(); };

  return (
    <div className="space-y-5">
      {me?.profile && (
        <section className="rounded-2xl border p-4 text-sm" data-testid="profile">
          <div className="font-semibold">{me.profile.full_name}, {me.profile.age}</div>
          <div className="text-slate-600">{me.profile.occupation} &middot; {me.profile.city} &middot; {me.profile.mobile}</div>
          <div className="text-xs text-slate-500 mt-1">{me.profile.account_kind} account {me.profile.account_number} &middot; IFSC {me.profile.ifsc}</div>
          {me.profile.age >= 60 && <div className="text-xs text-amber-800 bg-amber-50 rounded px-2 py-1 mt-2">Senior customer: slow checks start at a lower amount (an assumption, not measured).</div>}
        </section>
      )}
      <section className="rounded-2xl bg-slate-50 border p-4">
        <div className="text-xs text-slate-500">Available balance (simulated)</div>
        <div className="text-3xl font-semibold mt-1" data-testid="balance">{me ? rupees(me.balance) : '...'}</div>
        <button onClick={() => navigate('/send')} className="mt-4 w-full bg-[#1b7f8c] text-white rounded-lg py-2.5 font-medium flex items-center justify-center gap-2"><SendIcon size={16} /> Send money</button>
      </section>

      {me?.demo && (
        <section className="rounded-2xl border p-4 space-y-2">
          <div className="text-sm font-medium">Demo: load a user's payment history</div>
          <div className="flex gap-2">
            <select value={persona} onChange={(e) => setPersona(e.target.value)} className="border rounded-lg px-2 py-1.5 text-sm flex-1" aria-label="Persona">
              {personas.map((p) => <option key={p.name} value={p.name}>{p.name} ({p.payments} payments)</option>)}
            </select>
            <button onClick={loadPersona} className="px-3 py-1.5 rounded-lg bg-[#14325c] text-white text-sm">Load</button>
            <button onClick={reset} className="px-3 py-1.5 rounded-lg border text-sm">Reset</button>
          </div>
          {note && <p className="text-xs text-slate-600">{note}</p>}
        </section>
      )}
      {me?.demo && (
        <section className="rounded-2xl border p-4 space-y-2" data-testid="methods">
          <div className="text-sm font-medium">Demo: this customer's safety checks</div>
          <p className="text-xs text-slate-500">Switch one off and send the same payment again: the engine rebuilds its plan from what is left.</p>
          {me.methods.filter((m) => m.implemented).map((m) => (
            <label key={m.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={m.enrolled} onChange={async (e) => { await api.post('/demo/methods', { method: m.id, enrolled: e.target.checked }); await refresh(); }} />
              {m.name} <span className="text-xs text-slate-500">({m.tier}, about {m.seconds}s)</span>
            </label>
          ))}
        </section>
      )}
      {error && <div role="alert" className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}

      <section>
        <h2 className="text-sm font-medium mb-2">Payment history</h2>
        {rows.length === 0 && <p className="text-sm text-slate-500">No payments yet. Load a demo history above, or send one.</p>}
        <ul className="divide-y">
          {rows.slice(0, 20).map((r) => (
            <li key={r.id} className="py-2 flex items-center justify-between text-sm">
              <Link to={`/pay/${r.id}`} className="min-w-0"><div className="truncate">{r.payee_name || r.payee_vpa}</div><div className="text-xs text-slate-500">{new Date(r.created_at).toLocaleString()}</div></Link>
              <div className="text-right"><div>{rupees(r.amount)}</div><span className={`text-xs px-2 py-0.5 rounded-full ${STATUS_STYLE[r.status] || ''}`}>{r.status}</span></div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
