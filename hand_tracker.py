import os
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

class HandTracker:
    def __init__(self):
        # Matches your exact layout: looks inside the 'models' directory in root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.normpath(os.path.join(current_dir, 'models', 'hand_landmarker.task'))
        model2_path = os.path.normpath(os.path.join(current_dir, 'models', 'gesture_recognizer.task'))
        base_options = python.BaseOptions(model_asset_path=model_path)
        base_options2 = python.BaseOptions(model_asset_path=model2_path)
        options = vision.HandLandmarkerOptions(
            base_options=base_options,
            num_hands=2,
            running_mode=vision.RunningMode.IMAGE
        )
        self.detector = vision.HandLandmarker.create_from_options(options)
        options2 = vision.GestureRecognizerOptions(
            base_options=base_options2,
            running_mode=vision.RunningMode.IMAGE,
            num_hands=2
        )
        self.gesture_detector = vision.GestureRecognizer.create_from_options(options2)
        # Tracking connections map
        self.HAND_CONNECTIONS = [
            (0, 1), (1, 2), (2, 3), (3, 4),           # Thumb
            (0, 5), (5, 6), (6, 7), (7, 8),           # Index
            (0, 9), (9, 10), (10, 11), (11, 12),      # Middle
            (0, 13), (13, 14), (14, 15), (15, 16),    # Ring
            (0, 17), (17, 18), (18, 19), (19, 20),    # Pinky
            (5, 9), (9, 13), (13, 17),                # Palm base
        ]

    def find_hands(self, frame):
        h, w, _ = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        
        detection_result = self.detector.detect(mp_image)
        
        if detection_result.hand_landmarks:
            for hand_landmarks in detection_result.hand_landmarks:
                # Draw green lines connecting the joints
                for start_idx, end_idx in self.HAND_CONNECTIONS:
                    start_lm = hand_landmarks[start_idx]
                    end_lm = hand_landmarks[end_idx]
                    
                    x1, y1 = int(start_lm.x * w), int(start_lm.y * h)
                    x2, y2 = int(end_lm.x * w), int(end_lm.y * h)
                    
                    cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                
                # Draw red filled circles on individual joints
                for lm in hand_landmarks:
                    cx, cy = int(lm.x * w), int(lm.y * h)
                    cv2.circle(frame, (cx, cy), 4, (255, 0, 0), cv2.FILLED)
                    
        return frame

    def gesture_recognition(self, frame):
        h,w,_ = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        gesture_result = self.gesture_detector.detect(mp_image)