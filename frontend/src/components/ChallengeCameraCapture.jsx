// frontend/src/components/ChallengeCameraCapture.jsx
import React, { useRef, useState, useCallback, useEffect } from 'react';
import Webcam from 'react-webcam';
import { Camera } from 'lucide-react';
import * as tf from '@tensorflow/tfjs';
import * as blazeface from '@tensorflow-models/blazeface';

const CHALLENGE_PROMPTS = {
  blink: 'Blink naturally',
  turn_left: 'Turn your head to your left',
  turn_right: 'Turn your head to your right',
};

const BURST_FRAME_COUNT = 13;
const BURST_INTERVAL_MS = 150;

const ChallengeCameraCapture = ({ challengeType, onCapture, label = 'Verify Identity', error, onErrorClear }) => {
  const webcamRef = useRef(null);
  const [isFaceDetected, setIsFaceDetected] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [framesCaptured, setFramesCaptured] = useState(0);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let detector = null;
    let animationFrameId = null;

    const loadModelAndDetect = async () => {
      try {
        await tf.ready();
        detector = await blazeface.load();
        detectFace();
      } catch (err) {
        console.error('Failed to load face detection model', err);
      }
    };

    const detectFace = async () => {
      if (
        webcamRef.current &&
        webcamRef.current.video &&
        webcamRef.current.video.readyState === 4 &&
        !capturing &&
        !done
      ) {
        const video = webcamRef.current.video;
        const predictions = await detector.estimateFaces(video, false);
        setIsFaceDetected(predictions.length > 0);
      }
      animationFrameId = requestAnimationFrame(detectFace);
    };

    loadModelAndDetect();
    return () => {
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, [capturing, done]);

  const startBurst = useCallback(() => {
    if (onErrorClear) onErrorClear();
    setCapturing(true);
    setFramesCaptured(0);
    const frames = [];
    const intervalId = setInterval(() => {
      const shot = webcamRef.current?.getScreenshot();
      if (shot) {
        frames.push(shot);
        setFramesCaptured(frames.length);
      }
      if (frames.length >= BURST_FRAME_COUNT) {
        clearInterval(intervalId);
        setCapturing(false);
        setDone(true);
        onCapture(frames);
      }
    }, BURST_INTERVAL_MS);
  }, [onCapture, onErrorClear]);

  let borderColor = 'border-blue-400/50';
  if (error) borderColor = 'border-red-500';
  else if (isFaceDetected) borderColor = 'border-green-500';

  return (
    <div className="flex flex-col items-center space-y-4 w-full">
      <div className="relative w-full max-w-sm rounded-lg overflow-hidden bg-gray-100 aspect-[4/3] flex items-center justify-center">
        <Webcam
          audio={false}
          ref={webcamRef}
          screenshotFormat="image/jpeg"
          videoConstraints={{ width: 720, height: 540, facingMode: 'user' }}
          className="absolute inset-0 w-full h-full object-cover"
        />
        <div className={`absolute inset-0 pointer-events-none flex items-center justify-center border-4 border-dashed rounded-lg m-4 transition-colors duration-200 ${borderColor}`}>
          <span className="bg-black/50 text-white px-3 py-1 rounded text-sm mt-48 text-center">
            {done
              ? 'Captured - verifying...'
              : capturing
                ? `${CHALLENGE_PROMPTS[challengeType] || 'Hold still'} (${framesCaptured}/${BURST_FRAME_COUNT})`
                : isFaceDetected
                  ? `Ready - ${CHALLENGE_PROMPTS[challengeType] || 'press start'}`
                  : 'Position face here'}
          </span>
        </div>
      </div>

      {!capturing && !done && (
        <button
          type="button"
          onClick={startBurst}
          disabled={!isFaceDetected}
          className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          <Camera size={18} />
          <span>{label}</span>
        </button>
      )}
    </div>
  );
};

export default ChallengeCameraCapture;
