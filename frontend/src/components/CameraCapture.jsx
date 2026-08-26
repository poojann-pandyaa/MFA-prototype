import React, { useRef, useState, useCallback, useEffect } from 'react';
import Webcam from 'react-webcam';
import { Camera, RefreshCw } from 'lucide-react';
import * as tf from '@tensorflow/tfjs';
import * as blazeface from '@tensorflow-models/blazeface';

const CameraCapture = ({ onCapture, label = "Capture Face", error, onErrorClear }) => {
  const webcamRef = useRef(null);
  const [imgSrc, setImgSrc] = useState(null);
  const [isFaceDetected, setIsFaceDetected] = useState(false);

  useEffect(() => {
    let detector = null;
    let animationFrameId = null;

    const loadModelAndDetect = async () => {
      try {
        await tf.ready();
        detector = await blazeface.load();
        detectFace();
      } catch (err) {
        console.error("Failed to load face detection model", err);
      }
    };

    const detectFace = async () => {
      if (
        webcamRef.current &&
        webcamRef.current.video &&
        webcamRef.current.video.readyState === 4 &&
        !imgSrc
      ) {
        const video = webcamRef.current.video;
        const predictions = await detector.estimateFaces(video, false);
        
        if (predictions.length > 0) {
          setIsFaceDetected(true);
        } else {
          setIsFaceDetected(false);
        }
      }
      animationFrameId = requestAnimationFrame(detectFace);
    };

    loadModelAndDetect();

    return () => {
      if (animationFrameId) {
        cancelAnimationFrame(animationFrameId);
      }
    };
  }, [imgSrc]);

  const capture = useCallback(() => {
    const imageSrc = webcamRef.current.getScreenshot();
    setImgSrc(imageSrc);
    if (onErrorClear) onErrorClear();
  }, [webcamRef, onErrorClear]);

  const retake = () => {
    setImgSrc(null);
    if (onErrorClear) onErrorClear();
  };

  const handleConfirm = () => {
    if (imgSrc) {
      onCapture(imgSrc);
    }
  };

  // Determine dashed border color when live
  let liveBorderColor = 'border-blue-400/50';
  if (error) {
    liveBorderColor = 'border-red-500';
  } else if (isFaceDetected) {
    liveBorderColor = 'border-green-500';
  }

  return (
    <div className="flex flex-col items-center space-y-4 w-full">
      <div className="relative w-full max-w-sm rounded-lg overflow-hidden bg-gray-100 aspect-[4/3] flex items-center justify-center">
        {!imgSrc ? (
          <>
            <Webcam
              audio={false}
              ref={webcamRef}
              screenshotFormat="image/jpeg"
              videoConstraints={{
                width: 720,
                height: 540,
                facingMode: "user"
              }}
              className="absolute inset-0 w-full h-full object-cover"
            />
            {/* Overlay for face guidance */}
            <div className={`absolute inset-0 pointer-events-none flex items-center justify-center border-4 border-dashed rounded-lg m-4 transition-colors duration-200 ${liveBorderColor}`}>
              <span className="bg-black/50 text-white px-3 py-1 rounded text-sm mt-48">
                {isFaceDetected ? "Face detected - Ready!" : "Position face here"}
              </span>
            </div>
          </>
        ) : (
          <>
            <img src={imgSrc} alt="Captured" className="absolute inset-0 w-full h-full object-cover" />
            <div className={`absolute inset-0 pointer-events-none border-4 rounded-lg m-4 ${error ? 'border-red-500' : 'border-green-500'}`}></div>
          </>
        )}
      </div>

      <div className="flex space-x-4">
        {!imgSrc ? (
          <button
            type="button"
            onClick={capture}
            className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
          >
            <Camera size={18} />
            <span>Capture</span>
          </button>
        ) : (
          <>
            <button
              type="button"
              onClick={retake}
              className="flex items-center space-x-2 px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 transition-colors"
            >
              <RefreshCw size={18} />
              <span>Retake</span>
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              className="flex items-center space-x-2 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 transition-colors"
            >
              <span>{label}</span>
            </button>
          </>
        )}
      </div>
    </div>
  );
};

export default CameraCapture;
