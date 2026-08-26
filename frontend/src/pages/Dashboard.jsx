import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { LogOut, ShieldCheck, ShieldAlert, Shield } from 'lucide-react';

const API_URL = `http://${window.location.hostname}:8000`;

const Dashboard = () => {
  const navigate = useNavigate();
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('mfa_token');
    if (!token) {
      navigate('/login');
      return;
    }
    
    // Minimal way to get username from token for demo
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      setUsername(payload.sub);
      fetchHistory(payload.sub);
    } catch (e) {
      navigate('/login');
    }
  }, [navigate]);

  const fetchHistory = async (user) => {
    try {
      const response = await axios.get(`${API_URL}/history/${user}`);
      setHistory(response.data);
    } catch (err) {
      console.error("Failed to fetch history");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('mfa_token');
    // For demo purposes, we can choose to leave the device_id to simulate 
    // "known device". If we remove it, the next login will be high risk.
    // Let's NOT remove it here so the adaptive behavior is easier to test.
    // If a user wants to test HIGH risk, they should clear cookies/localStorage manually or use incognito.
    navigate('/login');
  };

  const getRiskIcon = (score) => {
    switch(score) {
      case 'LOW': return <ShieldCheck className="text-green-500" size={20} />;
      case 'MEDIUM': return <Shield className="text-yellow-500" size={20} />;
      case 'HIGH': return <ShieldAlert className="text-red-500" size={20} />;
      default: return null;
    }
  };

  if (loading) {
    return <div className="text-center mt-20">Loading dashboard...</div>;
  }

  return (
    <div className="bg-white rounded-xl shadow-md overflow-hidden">
      <div className="p-6 border-b border-gray-200 flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold text-gray-800">Welcome, {username}</h2>
          <p className="text-sm text-gray-500 mt-1">Your device ID: <span className="font-mono bg-gray-100 px-1 rounded">{localStorage.getItem('device_id')}</span></p>
        </div>
        <button 
          onClick={handleLogout}
          className="flex items-center space-x-2 px-4 py-2 text-gray-600 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 rounded-md transition-colors"
        >
          <LogOut size={18} />
          <span>Logout</span>
        </button>
      </div>

      <div className="p-6">
        <h3 className="text-lg font-semibold mb-4 text-gray-700">Login History</h3>
        
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Time</th>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Device ID</th>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Risk Score</th>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Face Checked?</th>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {history.map((record) => (
                <tr key={record.id} className={record.success ? "" : "bg-red-50"}>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {new Date(record.timestamp).toLocaleString()}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-mono text-gray-500">
                    {record.device_identifier}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center space-x-2">
                      {getRiskIcon(record.risk_score)}
                      <span className={`text-sm font-medium ${
                        record.risk_score === 'LOW' ? 'text-green-700' : 
                        record.risk_score === 'MEDIUM' ? 'text-yellow-700' : 'text-red-700'
                      }`}>
                        {record.risk_score}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {record.face_check_triggered ? (
                      <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-blue-100 text-blue-800">Yes</span>
                    ) : (
                      <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-gray-100 text-gray-800">No</span>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {record.success ? (
                      <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">Success</span>
                    ) : (
                      <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-red-100 text-red-800">Failed</span>
                    )}
                  </td>
                </tr>
              ))}
              {history.length === 0 && (
                <tr>
                  <td colSpan="5" className="px-6 py-4 text-center text-sm text-gray-500">
                    No login history found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
