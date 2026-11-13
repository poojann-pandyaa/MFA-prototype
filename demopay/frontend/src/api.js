import axios from 'axios';

export const API = import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8070`;

// The pre-registered customers' history was recorded on 'web-device', so by default this browser is their usual phone.
export const deviceId = () => localStorage.getItem('dp_device') || 'web-device';

const api = axios.create({ baseURL: API });
api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('dp_token');
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  cfg.headers['X-Device-Id'] = deviceId();
  return cfg;
});

export const errText = (e) => {
  const d = e?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x) => x.msg).join('; ');
  return d?.message || e?.message || 'Something went wrong';
};

export const rupees = (n) => 'Rs ' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });

export default api;
