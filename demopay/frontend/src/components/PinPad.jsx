import { useState } from 'react';
import { Delete } from 'lucide-react';

export default function PinPad({ onSubmit, busy, error }) {
  const [pin, setPin] = useState('');
  const add = (d) => setPin((p) => (p.length < 6 ? p + d : p));
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600">Enter your UPI PIN. <span className="text-amber-800">Demo: any 4 to 6 digits work; the real UPI PIN is not used.</span></p>
      <div className="text-center text-3xl tracking-[0.5em] h-10" aria-label="PIN entered" data-testid="pin-dots">{'•'.repeat(pin.length) || ' '}</div>
      {error && <div role="alert" className="p-2.5 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}
      <div className="grid grid-cols-3 gap-2">
        {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => <button key={n} onClick={() => add(String(n))} className="border rounded-lg py-3 text-lg">{n}</button>)}
        <button onClick={() => setPin('')} className="border rounded-lg py-3 text-sm">Clear</button>
        <button onClick={() => add('0')} className="border rounded-lg py-3 text-lg">0</button>
        <button onClick={() => setPin((p) => p.slice(0, -1))} aria-label="Delete" className="border rounded-lg py-3 flex items-center justify-center"><Delete size={18} /></button>
      </div>
      <button disabled={pin.length < 4 || busy} onClick={() => onSubmit(pin)} className="w-full bg-[#1b7f8c] text-white rounded-lg py-2.5 font-medium disabled:opacity-40">{busy ? 'Paying...' : 'Pay now'}</button>
    </div>
  );
}
