import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import CameraCapture from '../components/CameraCapture';

const API_URL = `http://${window.location.hostname}:8000`;

const Enroll = () => {
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState(1);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleNext = (e) => {
    e.preventDefault();
    if (!username || !password) {
      setError("Username and password are required");
      return;
    }
    setError('');
    setStep(2);
  };

  const handleEnroll = async (faceImageB64) => {
    setLoading(true);
    setError('');
    try {
      const response = await axios.post(`${API_URL}/enroll`, {
        username,
        password,
        face_image_b64: faceImageB64
      });
      if (response.data.access_token) {
        localStorage.setItem('mfa_token', response.data.access_token);
        // Set a simple device ID if not exists (for demo)
        if (!localStorage.getItem('device_id')) {
          localStorage.setItem('device_id', 'device_' + Math.random().toString(36).substr(2, 9));
        }
        navigate('/dashboard');
      }
    } catch (err) {
      setError(err.response?.data?.detail || "Enrollment failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-md mx-auto mt-10 bg-white p-8 rounded-xl shadow-md">
      <h2 className="text-2xl font-bold mb-6 text-center">Enrollment</h2>
      
      {error && <div className="mb-4 p-3 bg-red-100 text-red-700 rounded-md text-sm">{error}</div>}
      
      {step === 1 ? (
        <form onSubmit={handleNext} className="space-y-4">
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
            className="w-full py-2 px-4 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
          >
            Next: Face Capture
          </button>
        </form>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-gray-600 text-center mb-4">
            Please capture your face. This will be used for biometric verification if an unusual login is detected.
          </p>
          <CameraCapture 
            onCapture={handleEnroll} 
            label={loading ? "Processing..." : "Complete Enrollment"} 
            error={error}
            onErrorClear={() => setError('')}
          />
          <button 
            onClick={() => setStep(1)}
            disabled={loading}
            className="w-full py-2 px-4 text-gray-600 hover:text-gray-900 transition-colors"
          >
            Back
          </button>
        </div>
      )}
      
      <div className="mt-6 text-center text-sm">
        <a href="/login" className="text-blue-600 hover:underline">Already enrolled? Login</a>
      </div>
    </div>
  );
};

export default Enroll;
