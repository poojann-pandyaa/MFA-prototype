import { useCallback, useEffect, useMemo, useState } from 'react';
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import api from './api';
import { Ctx } from './ctx';
import Login from './pages/Login';
import Home from './pages/Home';
import Send from './pages/Send';
import Pay from './pages/Pay';
import XRay from './components/XRay';

function Shell() {
  const [me, setMe] = useState(null);
  const [xrayId, setXrayId] = useState(null);
  const navigate = useNavigate();
  const authed = !!localStorage.getItem('dp_token');

  const refresh = useCallback(async () => {
    try {
      setMe((await api.get('/me')).data);
    } catch {
      localStorage.removeItem('dp_token');
      setMe(null);
    }
  }, []);

  useEffect(() => { if (authed) refresh(); }, [authed, refresh]);

  const signOut = () => { localStorage.removeItem('dp_token'); setMe(null); setXrayId(null); navigate('/login'); };
  const value = useMemo(() => ({ me, refresh, xrayId, setXrayId, signOut }), [me, refresh, xrayId]);

  return (
    <Ctx.Provider value={value}>
      <div className="bg-amber-100 text-amber-900 text-xs text-center py-1.5 px-3" role="note">
        <strong>DEMO</strong> &middot; simulated bank, simulated money, simulated phone signals. Not a real bank or payment app.
      </div>
      <div className="max-w-6xl mx-auto p-4 grid gap-6 lg:grid-cols-[420px_1fr] items-start">
        <div className="rounded-3xl bg-white shadow-xl border border-slate-200 overflow-hidden min-h-[680px] flex flex-col">
          <header className="bg-[#14325c] text-white px-5 py-4 flex items-center justify-between">
            <Link to="/" className="flex items-center gap-2 font-semibold text-lg"><ShieldCheck size={20} /> DemoPay</Link>
            {me && <div className="text-right text-xs"><div className="opacity-80">{me.username}</div><button onClick={signOut} className="underline">Sign out</button></div>}
          </header>
          <main className="p-5 flex-1">
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/" element={authed ? <Home /> : <Navigate to="/login" replace />} />
              <Route path="/send" element={authed ? <Send /> : <Navigate to="/login" replace />} />
              <Route path="/pay/:id" element={authed ? <Pay /> : <Navigate to="/login" replace />} />
            </Routes>
          </main>
        </div>
        {me?.demo && <XRay />}
      </div>
    </Ctx.Provider>
  );
}

export default function App() {
  return <BrowserRouter><Shell /></BrowserRouter>;
}
