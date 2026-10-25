import threading
import time

import cv2

from camera_factory import create_camera
from detection_state_manager import DetectionStateManager
from face_recognition_manager import FaceRecognitionManager
from system_logger import system_logger
from tts_manager import TTSManager
from voice_event_manager import VoiceEventManager
from yolo_detector import YOLODetector


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

TEST_IMAGE_FOLDER = r"D:\AI_Project\New AI project\Test_Images"

# Use the ESP32-CAM for the next integration test.
# Change to "laptop" to return to the laptop camera.
CAMERA_SOURCE = "esp32"

# InsightFace is intentionally throttled because it can be
# CPU-intensive on the laptop.
FACE_RECOGNITION_INTERVAL = 2.0

# A recognized person must be absent from recognition results
# for this long before being removed from active_people.
#
# This prevents one temporary "No face detected" result from
# immediately resetting the person's active state.
FACE_DISAPPEARANCE_GRACE = 3.0

# Open a desktop preview with live YOLO and face boxes.
SHOW_CAMERA_WINDOW = True
CAMERA_WINDOW_NAME = "Smart AI Vision Assistant - Live Detection"
FACE_BOX_MAX_AGE_SECONDS = 4.0


# ---------------------------------------------------------
# Main Controller
# ---------------------------------------------------------

class MainController:
    """
    Central software controller for the Smart AI Vision Assistant.

    Current architecture:

        Laptop Camera
              |
              +----------------------+
              |                      |
              v                      v
        Unified YOLO          Latest Frame Buffer
              |                      |
              v                      v
        Object Events       Face Recognition Worker
              |                      |
              |                InsightFace
              |                      |
              |                Person Event
              |                      |
              +----------+-----------+
                         |
                         v
                  VoiceEventManager
                         |
                         v
                     TTSManager
                         |
                         v
                    SystemLogger

    Camera sources supported:

        "mock"
        "laptop"
        "esp32"
    """

    def __init__(self):

        system_logger.info(
            "[SYSTEM] Initializing Smart AI Vision Assistant..."
        )

        # -------------------------------------------------
        # Camera
        # -------------------------------------------------

        if CAMERA_SOURCE == "mock":

            from mock_camera import MockCamera

            self.camera = MockCamera(
                TEST_IMAGE_FOLDER
            )

        else:

            self.camera = create_camera(
                CAMERA_SOURCE
            )

        # -------------------------------------------------
        # AI / Processing Modules
        # -------------------------------------------------

        self.detector = YOLODetector()

        self.state_manager = DetectionStateManager(
            disappear_delay=1.0
        )

        self.face_recognizer = FaceRecognitionManager()

        self.voice_manager = VoiceEventManager()

        self.tts = TTSManager()

        # -------------------------------------------------
        # Controller State
        # -------------------------------------------------

        self.running = False

        # YOLO runs on a separate latest-frame worker so it cannot block
        # the live camera preview. Old queued frames are intentionally dropped.
        self.inference_thread = None
        self.inference_condition = threading.Condition()
        self.latest_inference_frame = None
        self.inference_worker_running = False
        self.latest_detections = []
        self.detections_lock = threading.Lock()

        # Live capture/inference telemetry. The dashboard reads these values;
        # capture timing is updated in the camera loop, inference timing in the AI worker.
        self.dashboard_frame_callback = None
        self.capture_fps = 0.0
        self._last_capture_monotonic = 0.0
        self.inference_latency_ms = 0.0
        self.ai_last_success_monotonic = 0.0
        self.ai_status = "STARTING"

        # -------------------------------------------------
        # Face Recognition Worker State
        # -------------------------------------------------

        self.face_worker_thread = None

        self.face_condition = threading.Condition()

        self.latest_face_frame = None

        self.face_frame_counter = 0

        self.face_processed_counter = 0

        self.face_last_start_time = 0.0

        self.face_worker_running = False

        # Latest InsightFace detections are produced by the background
        # worker and drawn by the main camera/display thread.
        self.face_annotations_lock = threading.Lock()
        self.face_annotations = []
        self.face_annotations_time = 0.0

        # -------------------------------------------------
        # Face Identity State
        # -------------------------------------------------
        #
        # Stores the last time each recognized person was
        # successfully seen by InsightFace.
        #
        # Example:
        #
        # {
        #     "Ateeb Baig": 12345.67
        # }
        #
        # VoiceEventManager.active_people controls whether
        # the person's arrival is announced.
        #
        # This dictionary controls when a person should be
        # considered to have actually disappeared.
        # -------------------------------------------------

        self.face_last_seen_people = {}

        system_logger.info(
            "[SYSTEM] Main controller initialized."
        )

    # =====================================================
    # FACE RECOGNITION WORKER
    # =====================================================

    def _start_face_worker(self):

        if self.face_worker_running:
            return

        self.face_worker_running = True

        self.face_worker_thread = threading.Thread(
            target=self._face_worker_loop,
            name="FaceRecognitionWorker",
            daemon=True
        )

        self.face_worker_thread.start()

        system_logger.info(
            "[FACE] Background recognition worker started."
        )

    def _submit_face_frame(self, frame):

        if not self.face_worker_running:
            return

        if frame is None:
            return

        with self.face_condition:

            # Always keep only the newest frame.
            self.latest_face_frame = frame.copy()

            self.face_frame_counter += 1

            self.face_condition.notify()

    def _face_worker_loop(self):

        next_allowed_time = 0.0

        while self.face_worker_running:

            frame = None

            # -------------------------------------------------
            # Wait for a frame
            # -------------------------------------------------

            with self.face_condition:

                while (
                    self.face_worker_running
                    and self.latest_face_frame is None
                ):

                    self.face_condition.wait(
                        timeout=0.5
                    )

                if not self.face_worker_running:
                    break

                frame = self.latest_face_frame

                self.latest_face_frame = None

            if frame is None:
                continue

            # -------------------------------------------------
            # Enforce recognition interval
            # -------------------------------------------------

            current_time = time.monotonic()

            if current_time < next_allowed_time:

                remaining = (
                    next_allowed_time
                    - current_time
                )

                if remaining > 0:

                    time.sleep(
                        min(
                            remaining,
                            0.25
                        )
                    )

                # Use a newer frame if one arrived.
                with self.face_condition:

                    if self.latest_face_frame is not None:

                        frame = self.latest_face_frame

                        self.latest_face_frame = None

            # -------------------------------------------------
            # Run InsightFace
            # -------------------------------------------------

            recognition_start = time.monotonic()

            self.face_last_start_time = recognition_start

            try:

                result = (
                    self.face_recognizer
                    .recognize_frame(frame)
                )

                self.face_processed_counter += 1
                self._update_face_annotations(result)

                recognition_time = (
                    time.monotonic()
                    - recognition_start
                )

                system_logger.info(
                    f"[FACE] Recognition completed "
                    f"in {recognition_time:.2f}s."
                )

                self._process_face_result(
                    result
                )

            except Exception as error:

                system_logger.error(
                    f"[FACE] Recognition worker error: "
                    f"{error}"
                )

            # -------------------------------------------------
            # Schedule next recognition
            # -------------------------------------------------

            next_allowed_time = (
                time.monotonic()
                + FACE_RECOGNITION_INTERVAL
            )

    # =====================================================
    # FACE IDENTITY STATE
    # =====================================================

    def _mark_person_seen(self, person_name):
        """
        Mark a recognized person as currently seen.

        The VoiceEventManager decides whether an arrival
        announcement should be spoken.
        """

        if not person_name:
            return

        now = time.monotonic()

        self.face_last_seen_people[
            person_name
        ] = now

    def _remove_disappeared_people(self):
        """
        Remove recognized people who have not been seen for
        FACE_DISAPPEARANCE_GRACE seconds.

        This is the missing connection between face recognition
        disappearance and VoiceEventManager.active_people.
        """

        if not self.face_last_seen_people:
            return

        now = time.monotonic()

        disappeared_people = []

        for person_name, last_seen in list(
            self.face_last_seen_people.items()
        ):

            elapsed = now - last_seen

            if elapsed >= FACE_DISAPPEARANCE_GRACE:

                disappeared_people.append(
                    person_name
                )

        for person_name in disappeared_people:

            self.face_last_seen_people.pop(
                person_name,
                None
            )

            # Tell VoiceEventManager that this person is
            # no longer active.
            self.voice_manager.person_disappeared(
                person_name
            )

            system_logger.info(
                f"[FACE] Person disappeared: "
                f"{person_name} "
                f"(not seen for "
                f"{FACE_DISAPPEARANCE_GRACE:.1f}s)"
            )

    def _clear_all_active_people(self):
        """
        Clear all active recognized people.

        Used during shutdown.
        """

        for person_name in list(
            self.face_last_seen_people.keys()
        ):

            self.voice_manager.person_disappeared(
                person_name
            )

        self.face_last_seen_people.clear()

    # =====================================================
    # FACE RESULT PROCESSING
    # =====================================================

    def _process_face_result(self, face_result):

        # -------------------------------------------------
        # Defensive type check
        # -------------------------------------------------
        #
        # This specifically protects against the previous:
        #
        # 'str' object has no attribute 'get'
        #
        # error.
        # -------------------------------------------------

        if not isinstance(face_result, dict):

            system_logger.error(
                "[FACE] Invalid recognition result type: "
                f"{type(face_result).__name__}"
            )

            self._remove_disappeared_people()

            return

        if not face_result:

            self._remove_disappeared_people()

            return

        if not face_result.get(
            "success",
            False
        ):

            system_logger.warning(
                "[FACE] Face recognition failed."
            )

            # A failed recognition call should NOT
            # immediately remove a person. The grace
            # period continues to protect the active state.
            self._remove_disappeared_people()

            return

        face_count = face_result.get(
            "face_count",
            0
        )

        # Make sure face_count is usable.
        if not isinstance(
            face_count,
            int
        ):

            try:
                face_count = int(face_count)

            except (
                TypeError,
                ValueError
            ):

                face_count = 0

        # -------------------------------------------------
        # No face
        # -------------------------------------------------

        if face_count == 0:

            system_logger.info(
                "[FACE] No face detected."
            )

            # IMPORTANT:
            #
            # Do not immediately clear active_people.
            #
            # Instead, check whether the person has been
            # absent long enough to be considered gone.
            self._remove_disappeared_people()

            return

        # -------------------------------------------------
        # Process detected faces
        # -------------------------------------------------

        results = face_result.get(
            "results",
            []
        )

        # Defensive protection against malformed results.
        if not isinstance(
            results,
            list
        ):

            system_logger.error(
                "[FACE] Invalid face results type: "
                f"{type(results).__name__}"
            )

            self._remove_disappeared_people()

            return

        recognized_people_this_cycle = set()

        for result in results:

            # -------------------------------------------------
            # Defensive protection against a malformed item.
            #
            # This is another direct protection against:
            # 'str' object has no attribute 'get'
            # -------------------------------------------------

            if not isinstance(
                result,
                dict
            ):

                system_logger.warning(
                    "[FACE] Ignoring invalid face result item: "
                    f"{type(result).__name__}"
                )

                continue

            recognized = result.get(
                "recognized",
                False
            )

            name = result.get(
                "name"
            )

            similarity = result.get(
                "similarity"
            )

            # ---------------------------------------------
            # Recognized person
            # ---------------------------------------------

            if recognized and name:

                recognized_people_this_cycle.add(
                    name
                )

                # Mark this person as currently visible.
                self._mark_person_seen(
                    name
                )

                if similarity is not None:

                    system_logger.info(
                        f"[FACE] Recognized: "
                        f"{name} | "
                        f"similarity="
                        f"{similarity:.3f}"
                    )

                else:

                    system_logger.info(
                        f"[FACE] Recognized: "
                        f"{name}"
                    )

                person_voice_event = (
                    self.voice_manager
                    .process_person_event(
                        name
                    )
                )

                if person_voice_event is None:
                    continue

                # Defensive type check.
                if not isinstance(
                    person_voice_event,
                    dict
                ):

                    system_logger.error(
                        "[FACE] Invalid person voice "
                        "event type: "
                        f"{type(person_voice_event).__name__}"
                    )

                    continue

                message = (
                    person_voice_event
                    .get("message")
                )

                # Person arrivals outrank ordinary object descriptions.
                # TTS can interrupt an object announcement for this message.
                priority = 1

                if not message:
                    continue

                system_logger.info(
                    f"[VOICE] {message}"
                )

                self.tts.speak(
                    message,
                    priority=priority
                )

            # ---------------------------------------------
            # Unknown person
            # ---------------------------------------------

            else:

                candidate = result.get(
                    "candidate"
                )

                if isinstance(
                    candidate,
                    dict
                ):

                    candidate_name = (
                        candidate.get(
                            "name"
                        )
                    )

                    candidate_similarity = (
                        candidate.get(
                            "similarity"
                        )
                    )

                    if (
                        candidate_name
                        and candidate_similarity
                        is not None
                    ):

                        system_logger.info(
                            f"[FACE] Unknown person | "
                            f"best candidate="
                            f"{candidate_name} | "
                            f"similarity="
                            f"{candidate_similarity:.3f}"
                        )

                    else:

                        system_logger.info(
                            "[FACE] Unknown person."
                        )

                else:

                    system_logger.info(
                        "[FACE] Unknown person."
                    )

        # -------------------------------------------------
        # Remove people who have disappeared
        # -------------------------------------------------
        #
        # Only recognized names are tracked.
        # Unknown people never enter active_people.
        # -------------------------------------------------

        self._remove_disappeared_people()

    # =====================================================
    # FACE BOXES FOR LIVE DISPLAY
    # =====================================================

    def _update_face_annotations(self, face_result):
        """Copy face boxes and labels from InsightFace's latest result."""
        annotations = []

        if isinstance(face_result, dict) and face_result.get("success"):
            results = face_result.get("results", [])
            if isinstance(results, list):
                for result in results:
                    if not isinstance(result, dict):
                        continue

                    bbox = result.get("bbox")
                    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                        continue

                    try:
                        x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
                    except (TypeError, ValueError):
                        continue

                    if x2 <= x1 or y2 <= y1:
                        continue

                    recognized = bool(result.get("recognized"))
                    name = result.get("name") if recognized else None
                    similarity = result.get("similarity")
                    label = str(name) if name else "UNKNOWN FACE"
                    if similarity is not None:
                        try:
                            label += f" {float(similarity):.2f}"
                        except (TypeError, ValueError):
                            pass

                    annotations.append({
                        "bbox": (x1, y1, x2, y2),
                        "label": label,
                        "recognized": recognized,
                    })

        with self.face_annotations_lock:
            self.face_annotations = annotations
            self.face_annotations_time = time.monotonic()

    def _annotate_face_boxes(self, frame):
        """Draw the latest face-recognition boxes without altering AI input."""
        now = time.monotonic()
        with self.face_annotations_lock:
            if now - self.face_annotations_time > FACE_BOX_MAX_AGE_SECONDS:
                annotations = []
            else:
                annotations = list(self.face_annotations)

        height, width = frame.shape[:2]
        for item in annotations:
            x1, y1, x2, y2 = item["bbox"]
            x1 = max(0, min(width - 1, x1))
            x2 = max(0, min(width - 1, x2))
            y1 = max(0, min(height - 1, y1))
            y2 = max(0, min(height - 1, y2))
            if x2 <= x1 or y2 <= y1:
                continue

            # Green = recognized identity; red = face detected but unknown.
            color = (0, 220, 0) if item["recognized"] else (0, 0, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = item["label"]
            font = cv2.FONT_HERSHEY_SIMPLEX
            scale = 0.48
            thickness = 1
            (label_width, label_height), baseline = cv2.getTextSize(
                label, font, scale, thickness
            )
            top = max(0, y1 - label_height - baseline - 6)
            right = min(width - 1, x1 + label_width + 8)
            bottom = min(height - 1, top + label_height + baseline + 6)
            cv2.rectangle(frame, (x1, top), (right, bottom), color, -1)
            cv2.putText(
                frame, label, (x1 + 4, max(label_height, bottom - baseline - 3)),
                font, scale, (255, 255, 255), thickness, cv2.LINE_AA
            )

    # =====================================================
    # DASHBOARD FRAME ANNOTATION
    # =====================================================

    def _annotate_detections(self, frame, detections):
        """
        Draw the current YOLO detections directly onto the
        frame that is exposed to the dashboard.

        The face-recognition worker receives its clean frame
        before this method runs, so annotations do not affect
        InsightFace processing.
        """

        if frame is None:
            return

        if not detections:
            return

        for detection in detections:

            bbox = detection.get("bbox")

            if not bbox or len(bbox) != 4:
                continue

            x1, y1, x2, y2 = bbox

            class_name = detection.get(
                "class_name",
                "object"
            )

            confidence = float(
                detection.get(
                    "confidence",
                    0.0
                )
            )

            zone = detection.get(
                "zone",
                "CENTER"
            )

            # Person gets a slightly thicker box.
            thickness = 1

            # Green for person, blue for other objects.
            if class_name == "person":
                box_color = (0, 220, 0)
            else:
                box_color = (255, 170, 0)

            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                box_color,
                thickness
            )

            label = (
                f"{class_name.replace('_', ' ')} "
                f"{confidence:.2f} | {zone}"
            )

            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 0.43
            label_thickness = 1

            (label_width, label_height), baseline = (
                cv2.getTextSize(
                    label,
                    font,
                    font_scale,
                    label_thickness
                )
            )

            label_top = max(
                0,
                y1 - label_height - baseline - 6
            )

            label_bottom = (
                label_top
                + label_height
                + baseline
                + 6
            )

            label_right = (
                x1
                + label_width
                + 8
            )

            cv2.rectangle(
                frame,
                (x1, label_top),
                (label_right, label_bottom),
                box_color,
                -1
            )

            cv2.putText(
                frame,
                label,
                (
                    x1 + 4,
                    label_bottom - baseline - 3
                ),
                font,
                font_scale,
                (255, 255, 255),
                label_thickness,
                cv2.LINE_AA
            )

    # =====================================================
    # FRAME PROCESSING
    # =====================================================

    def process_frame(self, frame):

        inference_started = time.monotonic()
        self.ai_status = "PROCESSING"

        if frame is None:

            system_logger.warning(
                "[CAMERA] Received empty frame."
            )

            return

        # -------------------------------------------------
        # Send latest frame to face worker
        # -------------------------------------------------

        self._submit_face_frame(
            frame
        )

        # =================================================
        # YOLO OBJECT DETECTION
        # =================================================

        detections = self.detector.detect(
            frame
        )

        # Publish only the newest detections for the independent preview.
        with self.detections_lock:
            self.latest_detections = list(detections or [])

        # Draw YOLO boxes on the same frame that the dashboard
        # receives. Face recognition already received a clean
        # copy above, so these annotations do not affect it.
        self._annotate_detections(
            frame,
            detections
        )

        self._annotate_face_boxes(frame)

        if detections:

            system_logger.info(
                f"[YOLO] Detected "
                f"{len(detections)} object(s)."
            )

        else:

            system_logger.info(
                "[YOLO] No objects detected."
            )

        # -------------------------------------------------
        # Detection State
        # -------------------------------------------------

        events = self.state_manager.update(
            detections
        )

        # -------------------------------------------------
        # Process Detection Events
        # -------------------------------------------------

        for event in events:

            event_type = event.get(
                "event"
            )

            class_name = event.get(
                "class_name",
                "object"
            )

            zone = event.get(
                "zone",
                "CENTER"
            )

            confidence = event.get(
                "confidence",
                0.0
            )

            if event_type == "NEW":

                system_logger.info(
                    f"[DETECTION] NEW: "
                    f"{class_name} | "
                    f"{zone} | "
                    f"{confidence:.2f}"
                )

            elif event_type == "PRESENT":

                pass

            elif event_type == "DISAPPEARED":

                system_logger.info(
                    f"[DETECTION] DISAPPEARED: "
                    f"{class_name}"
                )

            voice_event = (
                self.voice_manager
                .process_object_event(
                    event
                )
            )

            if voice_event is None:
                continue

            message = voice_event.get(
                "message"
            )

            priority = voice_event.get(
                "priority",
                2
            )

            if not message:
                continue

            system_logger.info(
                f"[VOICE] {message}"
            )

            self.tts.speak(
                message,
                priority=priority
            )

        self.inference_latency_ms = (time.monotonic() - inference_started) * 1000.0
        self.ai_last_success_monotonic = time.monotonic()
        self.ai_status = "ONLINE"

    # =====================================================
    # MAIN LOOP
    # =====================================================

    def _inference_worker_loop(self):
        """Process only the newest submitted frame; never build a backlog."""
        while self.inference_worker_running:
            with self.inference_condition:
                while (self.inference_worker_running and
                       self.latest_inference_frame is None):
                    self.inference_condition.wait(timeout=0.25)
                if not self.inference_worker_running:
                    break
                frame = self.latest_inference_frame
                self.latest_inference_frame = None

            if frame is not None:
                try:
                    self.process_frame(frame)
                except Exception as error:
                    system_logger.error(
                        f"[AI WORKER] Frame processing error: {error}"
                    )

    def _start_inference_worker(self):
        self.inference_worker_running = True
        self.inference_thread = threading.Thread(
            target=self._inference_worker_loop,
            name="YOLO-Inference-Worker",
            daemon=True,
        )
        self.inference_thread.start()
        system_logger.info("[YOLO] Background inference worker started.")

    def _stop_inference_worker(self):
        if not self.inference_worker_running:
            return
        self.inference_worker_running = False
        with self.inference_condition:
            self.latest_inference_frame = None
            self.inference_condition.notify_all()
        if self.inference_thread is not None and self.inference_thread.is_alive():
            self.inference_thread.join(timeout=5.0)
        self.inference_thread = None
        system_logger.info("[YOLO] Background inference worker stopped.")

    def run(self):
        self.running = True
        system_logger.info("[SYSTEM] Main processing loop started.")

        try:
            if CAMERA_SOURCE == "esp32":
                # Retry transient connection failures instead of exiting.
                while self.running:
                    system_logger.info("[CAMERA] Connecting to ESP32-CAM...")
                    if self.camera.connect():
                        system_logger.info("[CAMERA] ESP32-CAM stream connected.")
                        break
                    system_logger.warning(
                        "[CAMERA] Initial connection failed; retrying in 2 seconds."
                    )
                    time.sleep(2.0)
                if not self.running:
                    return

            self._start_face_worker()
            self._start_inference_worker()

            if SHOW_CAMERA_WINDOW:
                cv2.namedWindow(CAMERA_WINDOW_NAME, cv2.WINDOW_NORMAL)

            window_sized = False
            while self.running:
                frame = self.camera.read()
                if frame is None:
                    if CAMERA_SOURCE == "esp32":
                        if not self.camera.is_connected:
                            system_logger.warning(
                                "[CAMERA] ESP32 stream disconnected; attempting reconnect."
                            )
                            if not self.running:
                                break
                            if self.camera.reconnect(delay=1.0):
                                system_logger.info(
                                    "[CAMERA] ESP32-CAM reconnected; resuming processing."
                                )
                            else:
                                system_logger.warning(
                                    "[CAMERA] Reconnect failed; will retry in 2 seconds."
                                )
                                time.sleep(2.0)
                            continue
                        time.sleep(0.005)
                        continue
                    system_logger.warning("[CAMERA] No frame received. Stopping controller.")
                    break

                # Publish the newest clean camera frame immediately. This happens
                # before AI submission so slow YOLO/InsightFace work cannot delay the dashboard.
                captured_at = time.monotonic()
                if self._last_capture_monotonic:
                    instant_fps = 1.0 / max(0.001, captured_at - self._last_capture_monotonic)
                    self.capture_fps = instant_fps if self.capture_fps <= 0 else (0.75 * self.capture_fps + 0.25 * instant_fps)
                self._last_capture_monotonic = captured_at
                callback = self.dashboard_frame_callback
                if callback is not None:
                    try:
                        callback(frame)
                    except Exception as callback_error:
                        system_logger.warning(f"[DASHBOARD] Frame callback failed: {callback_error}")

                # Submit a copy to AI, replacing any stale frame waiting to be processed.
                with self.inference_condition:
                    self.latest_inference_frame = frame.copy()
                    self.inference_condition.notify()

                # Display the newest camera frame independently of YOLO/InsightFace.
                if SHOW_CAMERA_WINDOW:
                    display_frame = frame.copy()
                    with self.detections_lock:
                        detections = list(self.latest_detections)
                    self._annotate_detections(display_frame, detections)
                    self._annotate_face_boxes(display_frame)
                    if not window_sized:
                        height, width = display_frame.shape[:2]
                        cv2.resizeWindow(CAMERA_WINDOW_NAME, width * 2, height * 2)
                        window_sized = True
                    cv2.imshow(CAMERA_WINDOW_NAME, display_frame)
                    if (cv2.waitKey(1) & 0xFF) == ord("q"):
                        system_logger.info("[GUI] Q pressed. Closing live camera window.")
                        break

        except KeyboardInterrupt:
            system_logger.info("[SYSTEM] Keyboard interrupt received.")
        except Exception as error:
            system_logger.error(f"[SYSTEM] Controller error: {error}")
            raise
        finally:
            self.stop()

    # =====================================================
    # SHUTDOWN
    # =====================================================

    def _stop_face_worker(self):

        if not self.face_worker_running:
            return

        system_logger.info(
            "[FACE] Stopping recognition worker..."
        )

        self.face_worker_running = False

        with self.face_condition:

            self.latest_face_frame = None

            self.face_condition.notify_all()

        if (
            self.face_worker_thread is not None
            and self.face_worker_thread.is_alive()
        ):

            self.face_worker_thread.join(
                timeout=5.0
            )

        self.face_worker_thread = None

        system_logger.info(
            "[FACE] Recognition worker stopped."
        )

    def stop(self):

        if not self.running:
            return

        self.running = False

        system_logger.info(
            "[SYSTEM] Shutting down controller..."
        )

        # -------------------------------------------------
        # Stop inference worker
        # -------------------------------------------------

        try:
            self._stop_inference_worker()
        except Exception as error:
            system_logger.error(f"[YOLO] Worker shutdown error: {error}")

        # Stop face worker
        # -------------------------------------------------

        try:

            self._stop_face_worker()

        except Exception as error:

            system_logger.error(
                f"[FACE] Worker shutdown error: "
                f"{error}"
            )

        # -------------------------------------------------
        # Clear face identity state
        # -------------------------------------------------

        try:

            self._clear_all_active_people()

        except Exception as error:

            system_logger.error(
                f"[FACE] Identity state cleanup error: "
                f"{error}"
            )

        # -------------------------------------------------
        # Release camera
        # -------------------------------------------------

        try:

            self.camera.release()

        except Exception as error:

            system_logger.error(
                f"[CAMERA] Release error: "
                f"{error}"
            )

        # -------------------------------------------------
        # Wait for TTS
        # -------------------------------------------------

        try:

            self.tts.wait_until_done(
                timeout=10
            )

        except Exception as error:

            system_logger.error(
                f"[TTS] Wait error: "
                f"{error}"
            )

        # -------------------------------------------------
        # Stop TTS
        # -------------------------------------------------

        try:

            self.tts.stop()

        except Exception as error:

            system_logger.error(
                f"[TTS] Stop error: "
                f"{error}"
            )

        if SHOW_CAMERA_WINDOW:
            try:
                cv2.destroyAllWindows()
            except Exception as error:
                system_logger.warning(
                    f"[GUI] Could not close camera window cleanly: {error}"
                )

        system_logger.info(
            "[SYSTEM] Controller stopped."
        )


# ---------------------------------------------------------
# Program Entry Point
# ---------------------------------------------------------

if __name__ == "__main__":

    controller = MainController()

    controller.run()