import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { errText } from '../api';
import { useApp } from '../ctx';

// One-click demo scenarios. Each fills the form; the presenter then taps Pay. Best persona is noted.
const SCENARIOS = [
  { name: 'Routine payment', persona: 'asha', payee: 'priya.sharma@okaxis', amount: 300, hint: 'A friend you pay often. Expect no extra steps.' },
  { name: 'New local shop', persona: 'asha', payee: 'bakery.joy@ybl', amount: 450, hint: 'A new but genuine shop. Expect no extra steps.' },
  { name: 'Fake refund (blatant)', persona: 'asha', payee: 'refund.desk@ybl', amount: 9000, channel: 'collect', signals: { call_active: true, call_minutes: 6, call_unknown_number: true }, hint: 'A collect request from a 2-day-old reported account. Expect the payment to be stopped.' },
  { name: 'Digital arrest', persona: 'ramesh', payee: 'officer.verify@upi', amount: 20000, signals: { call_active: true, call_minutes: 25, call_unknown_number: true }, hint: 'A long call with an unknown number and a new, unreported account. Expect a few safety steps.' },
  { name: 'Digital arrest (large)', persona: 'ramesh', payee: 'officer.verify@upi', amount: 60000, signals: { call_active: true, call_minutes: 25, call_unknown_number: true }, hint: 'Same call, bigger amount: the engine judges it too risky for any available check and stops it.' },
  { name: 'Investment scam', persona: 'asha', payee: 'invest.profit99@ybl', amount: 20000, hint: 'A new investment-style account, no call.' },
  { name: 'Look-alike payee', persona: 'asha', payee: 'priya.sharrna@okaxis', amount: 4000, baseRate: '0.001', hint: 'One letter off a friend\'s id. Uses scam rarity 1 in 1,000 so the engine reacts.' },
  { name: 'Fake seller (often missed)', persona: 'kabir', payee: 'quickseller.deals@ybl', amount: 6000, channel: 'qr', hint: 'An account that looks ordinary. The model can miss this, and so will the app: an honest weak case.' },
  { name: 'Prize fee (often missed)', persona: 'asha', payee: 'lucky.draw.prize@ybl', amount: 3000, hint: 'A small fee to a fairly ordinary account. Often missed.' },
  { name: 'SIM swap + new phone', persona: 'asha', payee: 'unknown.account@ybl', amount: 60000, signals: { sim_changed_hours: 2, device_bound_hours: 1, pin_reset_hours: 1, old_device_active: false, bio_similarity: 0.3 }, hint: 'Stopped for now. Identity checks (second device, face) are planned and would let the owner prove it is them.' },
  { name: 'Remote control', persona: 'ramesh', payee: 'quickseller.deals@ybl', amount: 30000, signals: { remote_access_active: true, bio_similarity: 0.25 }, hint: 'Screen-share running with an unusual touch pattern. Stopped for now.' },
];

const emptySignals = { call: false, minutes: 30, unknown: true, remote: false, sim: false, simHours: 2, newPhone: false, bindHours: 1, pinReset: false, odd: false };

export default function Send() {
  const { me, setXrayId } = useApp();
  const navigate = useNavigate();
  const [payee, setPayee] = useState('');
  const [name, setName] = useState(null);
  const [amount, setAmount] = useState('');
  const [channel, setChannel] = useState('pay');
  const [fromContact, setFromContact] = useState(false);
  const [attested, setAttested] = useState(true);
  const [baseRate, setBaseRate] = useState('');
  const [sig, setSig] = useState(emptySignals);
  const [directory, setDirectory] = useState([]);
  const [hint, setHint] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { setXrayId(null); }, [setXrayId]);
  useEffect(() => { if (me?.demo) api.get('/demo/directory').then((r) => setDirectory(r.data)).catch(() => {}); }, [me?.demo]);
  useEffect(() => {
    if (payee.length < 3) { setName(null); return undefined; }
    const t = setTimeout(() => api.get('/directory/lookup', { params: { vpa: payee } }).then((r) => setName(r.data)).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [payee]);

  const apply = (s) => {
    setPayee(s.payee); setAmount(String(s.amount)); setChannel(s.channel || 'pay'); setBaseRate(s.baseRate || '');
    setHint(`Best with the "${s.persona}" history. ${s.hint}`);
    const x = s.signals || {};
    setSig({ call: !!x.call_active, minutes: x.call_minutes ?? 30, unknown: x.call_unknown_number ?? true, remote: !!x.remote_access_active,
      sim: x.sim_changed_hours != null, simHours: x.sim_changed_hours ?? 2, newPhone: x.device_bound_hours != null, bindHours: x.device_bound_hours ?? 1,
      pinReset: x.pin_reset_hours != null, odd: x.bio_similarity != null });
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    const signals = {};
    if (me?.demo) {
      if (sig.call) Object.assign(signals, { call_active: true, call_minutes: Number(sig.minutes), call_unknown_number: sig.unknown });
      if (sig.remote) signals.remote_access_active = true;
      if (sig.sim) signals.sim_changed_hours = Number(sig.simHours);
      if (sig.newPhone) Object.assign(signals, { device_bound_hours: Number(sig.bindHours), old_device_active: false });
      if (sig.pinReset) signals.pin_reset_hours = 1;
      if (sig.odd) signals.bio_similarity = 0.25;
    }
    try {
      const body = { payee_vpa: payee.trim(), amount: Number(amount), channel, requester_is_contact: fromContact, attested, signals };
      if (me?.demo && baseRate) body.base_rate = Number(baseRate);
      const r = await api.post('/pay/risk', body);
      navigate(`/pay/${r.data.id}`);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const Check = ({ k, label, children }) => (
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={sig[k]} onChange={(e) => setSig({ ...sig, [k]: e.target.checked })} />{label}{sig[k] && children}</label>
  );
  const Num = ({ k, unit }) => <span className="ml-1"><input type="number" min="0" className="w-16 border rounded px-1 py-0.5 text-xs" value={sig[k]} onChange={(e) => setSig({ ...sig, [k]: e.target.value })} /> {unit}</span>;

  return (
    <form onSubmit={submit} className="space-y-4">
      <h1 className="text-lg font-semibold">Send money</h1>
      {error && <div role="alert" className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}

      {me?.demo && (
        <section className="rounded-xl border p-3">
          <div className="text-xs font-medium mb-2">Demo scenarios (each fills the form)</div>
          <div className="flex flex-wrap gap-1.5">
            {SCENARIOS.map((s) => <button type="button" key={s.name} onClick={() => apply(s)} className="text-xs px-2 py-1 rounded-full border bg-slate-50 hover:bg-slate-100">{s.name}</button>)}
          </div>
          {hint && <p className="text-xs text-slate-600 mt-2" data-testid="hint">{hint}</p>}
        </section>
      )}

      <label className="block text-sm">Pay to (payee id)
        <input value={payee} onChange={(e) => setPayee(e.target.value)} required list="dir" className="mt-1 w-full border rounded-lg px-3 py-2" placeholder="name@bank" />
        <datalist id="dir">{directory.map((d) => <option key={d.vpa} value={d.vpa}>{d.label}</option>)}</datalist>
      </label>
      {name && (
        <div className={`text-xs rounded-lg px-3 py-2 ${name.found ? 'bg-green-50 text-green-800' : 'bg-amber-50 text-amber-800'}`}>
          {name.found ? <>Registered name: <strong>{name.registered_name}</strong></> : 'No registered name found for this payee id.'}
        </div>
      )}
      <label className="block text-sm">Amount (Rs)
        <input type="number" min="1" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required className="mt-1 w-full border rounded-lg px-3 py-2" />
      </label>
      <div className="flex gap-3 items-center text-sm">
        <label>Type <select value={channel} onChange={(e) => setChannel(e.target.value)} className="border rounded px-2 py-1 ml-1"><option value="pay">Pay</option><option value="collect">Collect request</option><option value="qr">Scanned QR</option></select></label>
        {channel === 'collect' && <label className="flex items-center gap-1"><input type="checkbox" checked={fromContact} onChange={(e) => setFromContact(e.target.checked)} /> requester is a saved contact</label>}
      </div>

      {me?.demo && (
        <details className="rounded-xl border p-3">
          <summary className="text-sm font-medium cursor-pointer">Demo: what the phone reports (you choose)</summary>
          <div className="mt-3 space-y-2">
            <p className="text-xs text-slate-500">A real app gets these from a trusted SDK or the telecom. Here the presenter sets them.</p>
            <Check k="call" label="On a phone call while paying"><Num k="minutes" unit="min" /></Check>
            <Check k="remote" label="Screen-share / remote control running" />
            <Check k="sim" label="SIM changed recently"><Num k="simHours" unit="h ago" /></Check>
            <Check k="newPhone" label="Brand-new phone (UPI just bound)"><Num k="bindHours" unit="h ago" /></Check>
            <Check k="pinReset" label="UPI PIN reset recently" />
            <Check k="odd" label="Touch pattern unlike the owner" />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} /> Phone SDK is trusted (off = server-only model)</label>
            <label className="text-sm block">Scam rarity in this traffic
              <select value={baseRate} onChange={(e) => setBaseRate(e.target.value)} className="border rounded px-2 py-1 ml-2 text-xs">
                <option value="">default (1 in 10,000)</option><option value="0.00001">1 in 100,000 (reported UPI rate)</option><option value="0.001">1 in 1,000 (high-risk)</option>
              </select>
            </label>
          </div>
        </details>
      )}

      <button disabled={busy} className="w-full bg-[#1b7f8c] text-white rounded-lg py-2.5 font-medium disabled:opacity-50">{busy ? 'Checking...' : 'Pay'}</button>
    </form>
  );
}
