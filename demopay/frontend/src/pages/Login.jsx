import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { errText } from '../api';
import { useApp } from '../ctx';

export default function Login() {
  const [mode, setMode] = useState('signup');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const { refresh } = useApp();

  const [users, setUsers] = useState(null);
  useEffect(() => { api.get('/demo/users').then((r) => setUsers(r.data)).catch(() => {}); }, []);

  const go = async (path, body) => {
    setBusy(true); setError('');
    try {
      const r = await api.post(path, body);
      localStorage.setItem('dp_token', r.data.token);
      await refresh();
      navigate('/');
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const r = await api.post(`/auth/${mode}`, { username, password });
      localStorage.setItem('dp_token', r.data.token);
      await refresh();
      navigate('/');
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
    {users && (
      <section className="space-y-2" data-testid="customers">
        <h1 className="text-xl font-semibold">Pick a customer</h1>
        <p className="text-sm text-slate-600">Four invented customers with their own payment history. Open one and try payments (password for all: <code>{users.password}</code>).</p>
        <div className="grid gap-2">
          {users.users.map((u) => (
            <button key={u.username} disabled={busy} onClick={() => go('/auth/login', { username: u.username, password: users.password })}
              className="text-left rounded-xl border p-3 hover:bg-slate-50 disabled:opacity-50">
              <div className="font-medium">{u.full_name} <span className="text-xs text-slate-500 font-normal">({u.username}, {u.age}, {u.city})</span></div>
              <div className="text-xs text-slate-600">{u.occupation}. {u.story}</div>
            </button>
          ))}
        </div>
      </section>
    )}
    <form onSubmit={submit} className="space-y-4">
      <h1 className="text-xl font-semibold">{mode === 'signup' ? 'Or create a blank demo account' : 'Or sign in with your own account'}</h1>
      <p className="text-sm text-slate-600">Demo accounts start with Rs 1,00,000 of simulated money. No real details are needed.</p>
      {error && <div role="alert" className="p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}
      <label className="block text-sm">Username
        <input className="mt-1 w-full border rounded-lg px-3 py-2" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required minLength={3} />
      </label>
      <label className="block text-sm">Password
        <input type="password" className="mt-1 w-full border rounded-lg px-3 py-2" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required minLength={6} />
      </label>
      <button disabled={busy} className="w-full bg-[#1b7f8c] text-white rounded-lg py-2.5 font-medium disabled:opacity-50">{busy ? 'Please wait...' : mode === 'signup' ? 'Create account' : 'Sign in'}</button>
      <button type="button" onClick={() => setMode(mode === 'signup' ? 'login' : 'signup')} className="w-full text-sm text-[#1b7f8c] underline">
        {mode === 'signup' ? 'I already have an account' : 'Create a new demo account'}
      </button>
    </form>
    </div>
  );
}
