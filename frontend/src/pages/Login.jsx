import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import CameraCapture from '../components/CameraCapture';

const API_URL = `http://${window.location.hostname}:8000`;

const Login = () => {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [riskInfo, setRiskInfo] = useState(null);

  // Generate or retrieve device ID
  const getDeviceId = () => {
    let deviceId = localStorage.getItem('device_id');
    if (!deviceId) {
      deviceId = 'device_' + Math.random().toString(36).substr(2, 9);
      // We don't save it to localStorage yet, only if login succeeds
    }
    return deviceId;
  };

  const handleStep1 = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const response = await axios.post(`${API_URL}/login/step1`, {
        username,
        password,
        device_identifier: getDeviceId()
      });
      
      const { require_step2, access_token, risk_score } = response.data;
      setRiskInfo(risk_score);

      if (require_step2) {
        setStep(2);
      } else {
        // Low risk, direct login
        localStorage.setItem('mfa_token', access_token);
        if (!localStorage.getItem('device_id')) {
          localStorage.setItem('device_id', getDeviceId());
        }
        navigate('/dashboard');
      }
    } catch (err) {
      setError(err.response?.data?.detail || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const handleStep2 = async (faceImageB64) => {
    setLoading(true);
    setError('');
    try {
      const response = await axios.post(`${API_URL}/login/step2`, {
        username,
        face_image_b64: faceImageB64,
        device_identifier: getDeviceId()
      });
      
      if (response.data.access_token) {
        localStorage.setItem('mfa_token', response.data.access_token);
        if (!localStorage.getItem('device_id')) {
          localStorage.setItem('device_id', getDeviceId());
        }
        navigate('/dashboard');
      }
    } catch (err) {
      setError(err.response?.data?.detail || "Biometric verification failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-md mx-auto mt-10 bg-white p-8 rounded-xl shadow-md">
      <h2 className="text-2xl font-bold mb-6 text-center">Login</h2>
      
      {error && <div className="mb-4 p-3 bg-red-100 text-red-700 rounded-md text-sm">{error}</div>}
      
      {step === 1 ? (
        <form onSubmit={handleStep1} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
            <input 
              type="text" 
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input 
              type="password" 
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
            />
          </div>
          <button 
            type="submit" 
            disabled={loading}
            className="w-full py-2 px-4 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors disabled:opacity-50"
          >
            {loading ? "Authenticating..." : "Login"}
          </button>
        </form>
      ) : (
        <div className="space-y-4">
          <div className="bg-yellow-50 border-l-4 border-yellow-400 p-4 mb-4">
            <div className="flex">
              <div className="flex-shrink-0">
                <svg className="h-5 w-5 text-yellow-400" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
              </div>
              <div className="ml-3">
                <p className="text-sm text-yellow-700">
                  <strong>{riskInfo} Risk Detected.</strong> Please verify your identity with a face scan.
                </p>
              </div>
            </div>
          </div>
          
          <CameraCapture 
            onCapture={handleStep2} 
            label={loading ? "Verifying..." : "Verify Identity"} 
            error={error}
            onErrorClear={() => setError('')}
          />
          <button 
            onClick={() => setStep(1)}
            disabled={loading}
            className="w-full py-2 px-4 text-gray-600 hover:text-gray-900 transition-colors mt-2"
          >
            Cancel
          </button>
        </div>
      )}
      
      <div className="mt-6 text-center text-sm">
        <a href="/enroll" className="text-blue-600 hover:underline">Don't have an account? Enroll</a>
      </div>
    </div>
  );
};

export default Login;
