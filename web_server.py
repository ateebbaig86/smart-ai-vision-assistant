from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

import cv2
import json
import threading
import time
from collections import OrderedDict
import urllib.request
import urllib.error
from pathlib import Path
import re

from main_controller import MainController
from face_enrollment_manager import FaceEnrollmentManager
from config import ESP32_IP
from system_event_bus import system_event_bus
from system_logger import system_logger


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "system.log"

MAX_LOG_HISTORY = 200
FILE_POLL_INTERVAL = 0.25

VIDEO_JPEG_QUALITY = 88
VIDEO_FPS_LIMIT = 15
ESP32_API_PORT = 81  # Separate from port 80, whose MJPEG handler stays open.


# =========================================================
# FLASK APPLICATION
# =========================================================

app = Flask(__name__)


# =========================================================
# SHARED APPLICATION STATE
# =========================================================

controller = None
controller_thread = None

latest_frame = None
latest_frame_lock = threading.Lock()
latest_frame_updated_monotonic = 0.0

controller_started = False
controller_start_error = None

# Dashboard language preference (shared with API clients).
SUPPORTED_LANGUAGES = {"en": "English", "hi": "Hindi", "kn": "Kannada"}
selected_speech_language = "en"
language_lock = threading.Lock()

# Dashboard telemetry and face enrollment state.
enrollment_manager = None
enrollment_lock = threading.Lock()
enrollment_worker = None
enrollment_last_result = {"success": True, "message": "Ready", "samples": 0, "required_samples": 5}
telemetry_cache = {"distance_cm": None, "state": "UNKNOWN", "updated_at": 0.0, "error": "Waiting for ESP32 distance data"}
telemetry_lock = threading.Lock()
gps_cache = {"valid": False, "latitude": None, "longitude": None, "accuracy_m": None, "age_ms": None, "updated_at": 0.0, "message": "Waiting for GPS fix"}
gps_lock = threading.Lock()
detection_history = OrderedDict()
detection_history_lock = threading.Lock()
MAX_DETECTION_HISTORY = 8


def _read_esp32_json(path, timeout=3.0):
    """Read one small ESP32 JSON endpoint, always closing the socket."""
    request = urllib.request.Request(
        f"http://{ESP32_IP}:{ESP32_API_PORT}{path}",
        headers={"Accept": "application/json", "Connection": "close", "Cache-Control": "no-cache"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(8192)
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not return a JSON object")
    return payload


def _telemetry_worker():
    """Poll distance and GPS sequentially to avoid competing ESP32 clients.

    The ESP32 also carries a long-lived MJPEG stream. Two independent polling
    threads at sub-second intervals can exhaust its HTTP client slots. This
    single worker performs one request at a time and preserves last-good data
    through brief socket resets.
    """
    global telemetry_cache, gps_cache
    while True:
        cycle_started = time.monotonic()
        # Distance: exact fields from the confirmed firmware JSON response.
        try:
            payload = _read_esp32_json("/distance", timeout=1.5)
            if payload.get("valid") is not True or payload.get("sensor_ready") is False:
                raise ValueError(payload.get("message") or "ESP32 distance sensor is not ready")
            distance = float(payload.get("distance_cm"))
            if not 2 <= distance <= 400:
                raise ValueError(f"Distance outside valid range: {distance}")
            raw_zone = str(payload.get("zone", "")).lower()
            state = {"critical": "CRITICAL", "warning": "WARNING", "caution": "CAUTION", "clear": "SAFE", "safe": "SAFE"}.get(
                raw_zone,
                "CRITICAL" if distance < 30 else "WARNING" if distance < 60 else "CAUTION" if distance <= 100 else "SAFE",
            )
            with telemetry_lock:
                telemetry_cache = {
                    "distance_cm": round(distance, 1), "state": state,
                    "buzzer": bool(payload.get("buzzer", False)),
                    "sensor_ready": bool(payload.get("sensor_ready", True)),
                    "updated_at": time.time(), "error": None, "raw": payload,
                }
        except Exception as exc:
            with telemetry_lock:
                # Keep the last valid measurement visible; expose the transient
                # transport error separately instead of erasing good sensor data.
                telemetry_cache = {**telemetry_cache, "error": f"{type(exc).__name__}: {exc}", "last_error_at": time.time()}
                last_ok = float(telemetry_cache.get("updated_at", 0) or 0)
                if not last_ok or time.time() - last_ok > 15:
                    telemetry_cache["state"] = "STALE"

        # GPS is requested only after the distance connection has closed.
        try:
            payload = _read_esp32_json("/gps", timeout=1.5)
            if not payload.get("valid"):
                raise ValueError(payload.get("message") or "Waiting for GPSLogger fix")
            lat, lon = float(payload["latitude"]), float(payload["longitude"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("GPS coordinates outside valid ranges")
            with gps_lock:
                gps_cache = {
                    "valid": True, "latitude": lat, "longitude": lon,
                    "accuracy_m": payload.get("accuracy_m"), "age_ms": payload.get("age_ms"),
                    "updated_at": time.time(), "message": "GPS fix received", "error": None,
                }
        except Exception as exc:
            with gps_lock:
                gps_cache = {**gps_cache, "error": f"{type(exc).__name__}: {exc}", "last_error_at": time.time()}
                last_ok = float(gps_cache.get("updated_at", 0) or 0)
                # A GPS fix is phone-sourced and may be tens of seconds old.
                # Keep coordinates usable for up to 2 minutes through brief resets.
                if not last_ok or time.time() - last_ok > 120:
                    gps_cache["valid"] = False
                    gps_cache["message"] = "GPS poll failed; waiting for next fix"

        # Keep a modest interval; dashboard browser requests read only cache.
        elapsed = time.monotonic() - cycle_started
        time.sleep(max(0.15, 1.0 - elapsed))


def _enrollment_worker_loop():
    """Capture enrollment samples from the already-open controller camera frames."""
    global enrollment_last_result
    while True:
        try:
            with enrollment_lock:
                manager = enrollment_manager
                active = bool(manager and manager.active)
            if active:
                with latest_frame_lock:
                    frame = latest_frame.copy() if latest_frame is not None else None
                if frame is not None:
                    result = manager.process_frame(frame, capture=True)
                    with enrollment_lock:
                        enrollment_last_result = result
                    if result.get("captured"):
                        system_logger.info(f"[FACE ENROLL] {result.get('message', 'Sample captured')}")
                    if result.get("completed"):
                        try:
                            if controller is not None and getattr(controller, "face_recognizer", None) is not None:
                                controller.face_recognizer.reload_database()
                        except Exception as reload_exc:
                            system_logger.error(f"[FACE ENROLL] Database reload failed: {reload_exc}")
                        system_logger.info(f"[FACE ENROLL] {result.get('message', 'Enrollment complete')}")
                else:
                    with enrollment_lock:
                        enrollment_last_result = {"success": False, "message": "Waiting for camera frame", "samples": len(manager.embeddings), "required_samples": manager.NUM_SAMPLES}
        except Exception as exc:
            with enrollment_lock:
                enrollment_last_result = {"success": False, "message": str(exc), "samples": 0, "required_samples": 5}
            time.sleep(0.5)
        time.sleep(0.7)



# =========================================================
# CONTROLLER FRAME BRIDGE
# =========================================================

def publish_dashboard_frame(frame):
    """Publish fresh frames; pause normal AI processing during face enrollment."""
    global latest_frame, latest_frame_updated_monotonic
    if frame is None:
        return False
    with latest_frame_lock:
        latest_frame = frame.copy()
        latest_frame_updated_monotonic = time.monotonic()
    with enrollment_lock:
        manager = enrollment_manager
        enrollment_active = bool(manager and manager.active)
    return not enrollment_active
# =========================================================
# START MAIN CONTROLLER
# =========================================================

def start_controller():
    """
    Start MainController in a background thread.

    The controller owns the camera and the AI pipeline.
    Flask only consumes the latest frame.
    """

    global controller
    global controller_thread
    global controller_started
    global controller_start_error

    try:

        print(
            "[WEB] Initializing MainController..."
        )

        controller = MainController()
        with language_lock:
            initial_language = selected_speech_language
        controller.tts.set_language(initial_language)

        # Camera loop publishes raw frames immediately, independently of AI.
        # This avoids the old dashboard lag caused by publishing only after YOLO.
        controller.dashboard_frame_callback = publish_dashboard_frame

        controller_started = True

        print(
            "[WEB] MainController initialized successfully."
        )

        print(
            "[WEB] Starting AI processing loop..."
        )

        controller.run()

    except Exception as error:

        controller_start_error = str(error)

        controller_started = False

        print(
            f"[WEB] MainController error: {error}"
        )

        try:

            system_logger.error(
                f"[WEB] MainController error: {error}"
            )

        except Exception:
            pass


# =========================================================
# LOG PARSER
# =========================================================

def parse_log_line(line):
    """
    Convert one system.log line into the same event structure
    used by the dashboard EventSource.
    """

    line = line.strip()

    if not line:
        return None

    try:

        # -------------------------------------------------
        # Extract timestamp
        # -------------------------------------------------

        if not line.startswith("["):
            return None

        timestamp_end = line.find("]")

        if timestamp_end == -1:
            return None

        timestamp = line[
            1:timestamp_end
        ]

        remaining = line[
            timestamp_end + 1:
        ].strip()

        # -------------------------------------------------
        # Extract log level
        # -------------------------------------------------

        if not remaining.startswith("["):
            return None

        level_end = remaining.find("]")

        if level_end == -1:
            return None

        level = remaining[
            1:level_end
        ].upper()

        message = remaining[
            level_end + 1:
        ].strip()

        # -------------------------------------------------
        # Extract category from message
        # -------------------------------------------------

        category = "SYSTEM"

        if message.startswith("["):

            category_end = message.find("]")

            if category_end != -1:

                category = message[
                    1:category_end
                ].upper()

        return {
            "timestamp": timestamp,
            "level": level,
            "category": category,
            "message": message,
        }

    except Exception:

        return None


# =========================================================
# READ LOG HISTORY
# =========================================================

def read_log_history(limit=MAX_LOG_HISTORY):

    if not LOG_FILE.exists():
        return []

    try:

        with LOG_FILE.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as file:

            lines = file.readlines()

        events = []

        for line in lines[-limit:]:

            event = parse_log_line(line)

            if event is not None:
                events.append(event)

        return events

    except Exception as error:

        print(
            f"[WEB] Log history read error: {error}"
        )

        return []


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# =========================================================
# LIVE CAMERA STREAM
# =========================================================

@app.route("/video_feed")
def video_feed():
    """
    Browser-compatible MJPEG stream.

    This endpoint does NOT open a camera.

    It reads frames produced by MainController.
    """

    @stream_with_context
    def generate():

        last_sent_time = 0.0

        while True:

            # -------------------------------------------------
            # Limit dashboard streaming rate.
            # -------------------------------------------------

            current_time = time.monotonic()

            minimum_interval = (
                1.0 / VIDEO_FPS_LIMIT
            )

            elapsed = (
                current_time
                - last_sent_time
            )

            if elapsed < minimum_interval:

                time.sleep(
                    minimum_interval - elapsed
                )

            # -------------------------------------------------
            # Copy latest frame.
            # -------------------------------------------------

            with latest_frame_lock:

                if latest_frame is None:

                    frame = None

                else:

                    frame = latest_frame.copy()

            # -------------------------------------------------
            # No frame available yet.
            # -------------------------------------------------

            if frame is None:

                time.sleep(0.05)

                continue

            # Draw the latest AI annotations onto a copy of the freshest camera
            # frame. Detection results may be older than the image by one inference
            # cycle, but the video itself remains live and does not queue behind AI.
            active_controller = controller
            if active_controller is not None:
                try:
                    with active_controller.detections_lock:
                        current_detections = list(active_controller.latest_detections)
                    active_controller._annotate_detections(frame, current_detections)
                    active_controller._annotate_face_boxes(frame)
                except Exception as annotation_error:
                    system_logger.debug(f"[DASHBOARD] Annotation skipped: {annotation_error}")

            # -------------------------------------------------
            # JPEG encode.
            # -------------------------------------------------

            success, encoded = cv2.imencode(
                ".jpg",
                frame,
                [
                    cv2.IMWRITE_JPEG_QUALITY,
                    VIDEO_JPEG_QUALITY,
                ],
            )

            if not success:

                time.sleep(0.05)

                continue

            frame_bytes = encoded.tobytes()

            # -------------------------------------------------
            # Send MJPEG frame.
            # -------------------------------------------------

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame_bytes
                + b"\r\n"
            )

            last_sent_time = time.monotonic()

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# =========================================================
# CAMERA STATUS
# =========================================================

@app.route("/api/camera")
def get_camera_status():

    with latest_frame_lock:
        frame_available = latest_frame is not None
        frame_updated_at = latest_frame_updated_monotonic

    frame_age = (
        max(0.0, time.monotonic() - frame_updated_at)
        if frame_available and frame_updated_at > 0
        else None
    )

    active = controller
    source_connected = bool(frame_available)
    fps = 0.0
    latency_ms = 0.0
    ai_status = "OFFLINE"
    if active is not None:
        fps = round(float(getattr(active, "capture_fps", 0.0) or 0.0), 1)
        latency_ms = round(float(getattr(active, "inference_latency_ms", 0.0) or 0.0), 1)
        ai_status = str(getattr(active, "ai_status", "STARTING"))
        if not controller_started:
            ai_status = "OFFLINE"
    return jsonify({
        "success": True,
        "frame_available": frame_available,
        "camera_status": "CONNECTED" if source_connected else "DISCONNECTED",
        "controller_started": controller_started,
        "controller_error": controller_start_error,
        "camera_fps": fps if source_connected else 0.0,
        "processing_latency_ms": latency_ms,
        "frame_age_seconds": round(frame_age, 2) if frame_age is not None else None,
        "ai_status": ai_status,
    })


# =========================================================
# SYSTEM LOG HISTORY
# =========================================================

@app.route("/api/logs")
def get_logs():

    file_events = read_log_history()

    return jsonify({
        "success": True,
        "events": file_events,
    })


# =========================================================
# LIVE SYSTEM LOG STREAM
# =========================================================

@app.route("/api/logs/stream")
def stream_logs():

    @stream_with_context
    def event_stream():

        try:

            if LOG_FILE.exists():

                file_handle = LOG_FILE.open(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                )

                file_handle.seek(
                    0,
                    2,
                )

            else:

                file_handle = None

        except Exception as error:

            print(
                f"[WEB] Could not open log file: {error}"
            )

            file_handle = None

        try:

            while True:

                # -----------------------------------------
                # Re-open file if necessary
                # -----------------------------------------

                if file_handle is None:

                    if LOG_FILE.exists():

                        try:

                            file_handle = LOG_FILE.open(
                                "r",
                                encoding="utf-8",
                                errors="replace",
                            )

                            file_handle.seek(
                                0,
                                2,
                            )

                        except Exception:

                            file_handle = None

                    time.sleep(
                        FILE_POLL_INTERVAL
                    )

                    yield ": heartbeat\n\n"

                    continue

                # -----------------------------------------
                # Read newly appended lines
                # -----------------------------------------

                new_line_found = False

                while True:

                    line = file_handle.readline()

                    if not line:
                        break

                    new_line_found = True

                    event = parse_log_line(
                        line
                    )

                    if event is None:
                        continue

                    yield (
                        "data: "
                        + json.dumps(event)
                        + "\n\n"
                    )

                # -----------------------------------------
                # Keep connection alive.
                # -----------------------------------------

                if not new_line_found:

                    yield ": heartbeat\n\n"

                time.sleep(
                    FILE_POLL_INTERVAL
                )

        finally:

            if file_handle is not None:

                try:
                    file_handle.close()

                except Exception:
                    pass

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =========================================================
# TEMPORARY LIVE LOG TEST
# =========================================================

@app.route("/api/test-log")
def test_log():

    system_logger.info(
        "[TEST] Live dashboard connection working"
    )

    return jsonify({
        "success": True,
        "message": "Test log generated.",
    })


# =========================================================
# SYSTEM STATUS
# =========================================================

@app.route("/api/status")
def get_status():

    return jsonify({
        "success": True,
        "system": "Smart AI Vision Assistant",
        "status": (
            "ONLINE"
            if controller_started
            else "STARTING"
        ),
        "controller_error": controller_start_error,
    })


# =========================================================
# LIVE DASHBOARD DATA
# =========================================================

@app.route("/api/dashboard-data")
def dashboard_data():
    current = []
    face_person, face_similarity, face_status = "--", "--", "Waiting"
    if controller is not None:
        try:
            with enrollment_lock:
                enrolling = bool(enrollment_manager and enrollment_manager.active)
            if not enrolling:
                with controller.detections_lock:
                    raw_detections = list(controller.latest_detections)
                # Display a current snapshot, not a counter incremented every AI
                # frame. Group current boxes by class and show how many instances
                # of each class are visible in this inference result.
                grouped = OrderedDict()
                for item in raw_detections:
                    name = str(item.get("class_name", "object"))
                    try:
                        conf = float(item.get("confidence", 0.0))
                    except (TypeError, ValueError):
                        conf = 0.0
                    entry = grouped.setdefault(name, {"name": name, "count": 0, "confidence": 0.0})
                    entry["count"] += 1
                    entry["confidence"] = max(entry["confidence"], conf)
                current = [{**item, "confidence": round(item["confidence"], 3)} for item in grouped.values()]
            with controller.face_annotations_lock:
                annotations = list(controller.face_annotations)
                annotation_age = time.monotonic() - float(controller.face_annotations_time or 0)
            if annotations and annotation_age <= 5.0 and not enrolling:
                item = annotations[0]
                label_text = str(item.get("label", "")).strip()
                face_person = str(item.get("name", label_text or "Unknown")).strip()
                sim = item.get("similarity", item.get("score", item.get("confidence")))
                # Some controller builds put the score into the label string,
                # e.g. "Ateeb Baig | similarity=0.725" or "Ateeb Baig 0.725".
                # Inspect the label as well when name is separately available.
                score_source = face_person if re.search(r"similarity\s*[:=]|\s+[01]\.\d{2,4}\s*$", face_person, re.I) else label_text
                match = re.search(r"\s*\|?\s*similarity\s*[:=]\s*([0-9]*\.?[0-9]+)\s*$", score_source, re.I)
                if match:
                    if sim is None:
                        sim = match.group(1)
                    extracted_name = score_source[:match.start()].strip(" |:-")
                    if extracted_name:
                        face_person = extracted_name
                elif sim is None:
                    match = re.search(r"\s+([01]\.\d{2,4})\s*$", score_source)
                    if match:
                        sim = match.group(1)
                        extracted_name = score_source[:match.start()].strip()
                        if extracted_name:
                            face_person = extracted_name
                face_similarity = f"{float(sim):.3f}" if sim is not None else "--"
                face_status = "Recognized" if face_person not in ("Unknown", "UNKNOWN", "Unknown person", "--") else "Unknown person"
            elif enrolling:
                face_status = "Enrollment mode — normal recognition paused"
            elif controller.face_worker_running:
                face_status = "Scanning"
        except Exception as exc:
            system_logger.warning(f"[WEB] Dashboard state read error: {exc}")
    with telemetry_lock:
        telemetry = dict(telemetry_cache)
    if not telemetry.get("updated_at") or time.time() - telemetry["updated_at"] > 15.0:
        telemetry["state"] = "STALE"
    telemetry["age_seconds"] = round(max(0.0, time.time() - float(telemetry.get("updated_at", 0) or 0)), 1) if telemetry.get("updated_at") else None
    with gps_lock:
        gps = dict(gps_cache)
    if gps.get("valid") and gps.get("age_ms") is not None and float(gps["age_ms"]) > 300000:
        gps["message"] = "Last GPS fix is older than 5 minutes"
    gps["poll_age_seconds"] = round(max(0.0, time.time() - float(gps.get("updated_at", 0) or 0)), 1) if gps.get("updated_at") else None
    with enrollment_lock:
        enroll_status = enrollment_manager.get_status() if enrollment_manager is not None else {"active": False, "samples": 0, "required_samples": 5, "name": None}
    camera_stats = get_camera_status().get_json()
    return jsonify({"success": True, "detections": current, "distance": telemetry, "gps": gps, "face": {"person": face_person, "similarity": face_similarity, "status": face_status}, "enrollment": enroll_status, "controller_started": controller_started, "camera": camera_stats})


@app.route("/api/esp32-check")
def esp32_check():
    """Diagnostic endpoint showing live reachability and the exact ESP32 JSON payloads."""
    result = {"success": True, "esp32_ip": ESP32_IP, "distance": None, "gps": None, "errors": {}}
    for key, path in (("distance", "/distance"), ("gps", "/gps")):
        try:
            with urllib.request.urlopen(f"http://{ESP32_IP}:{ESP32_API_PORT}{path}", timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            result[key] = payload
        except Exception as exc:
            result["errors"][key] = f"{type(exc).__name__}: {exc}"
    with telemetry_lock:
        result["distance_worker"] = dict(telemetry_cache)
    with gps_lock:
        result["gps_worker"] = dict(gps_cache)
    return jsonify(result)


@app.route("/api/gps")
def gps_status():
    with gps_lock:
        return jsonify({"success": True, **dict(gps_cache)})


@app.route("/api/enrollment/status")
def enrollment_status():
    with enrollment_lock:
        manager = enrollment_manager
        result = dict(enrollment_last_result)
        status = manager.get_status() if manager is not None else {"active": False, "name": None, "samples": 0, "required_samples": 5, "people_count": 0}
    return jsonify({"success": True, "status": status, "result": result})


@app.route("/api/enrollment/start", methods=["POST"])
def enrollment_start():
    global enrollment_manager, enrollment_last_result
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    source = str(payload.get("source", "esp32")).lower()
    if source != "esp32":
        return jsonify({"success": False, "message": "This integrated setup uses the ESP32-CAM as the single camera source. Select Smart Glasses / ESP32-CAM."}), 400
    if not name:
        return jsonify({"success": False, "message": "Enter a person's name first."}), 400
    try:
        with enrollment_lock:
            if enrollment_manager is None:
                enrollment_manager = FaceEnrollmentManager()
            result = enrollment_manager.start_enrollment(name, overwrite=False)
            enrollment_last_result = dict(result)
        if not result.get("success"):
            return jsonify(result), 409 if result.get("already_exists") else 400
        system_logger.info(f"[FACE ENROLL] Enrollment requested for {name}; hold one face in view for 5 samples.")
        return jsonify(result)
    except Exception as exc:
        system_logger.error(f"[FACE ENROLL] Could not start enrollment: {exc}")
        return jsonify({"success": False, "message": str(exc)}), 500


@app.route("/api/enrollment/cancel", methods=["POST"])
def enrollment_cancel():
    global enrollment_last_result
    with enrollment_lock:
        if enrollment_manager is None:
            return jsonify({"success": False, "message": "No enrollment session is active."}), 400
        result = enrollment_manager.cancel_enrollment()
        enrollment_last_result = dict(result)
    return jsonify(result)


# =========================================================
# MULTILINGUAL SPEECH LANGUAGE SETTING
# =========================================================

@app.route("/api/language", methods=["GET", "POST"])
def speech_language():
    """Get or set the live speech language used by MainController's TTS manager."""
    global selected_speech_language

    if request.method == "GET":
        with language_lock:
            language = selected_speech_language
        return jsonify({
            "success": True,
            "language": language,
            "label": SUPPORTED_LANGUAGES[language],
            "speech_engine_integrated": True,
        })

    payload = request.get_json(silent=True) or {}
    language = str(payload.get("language", "")).lower().strip()
    if language not in SUPPORTED_LANGUAGES:
        return jsonify({
            "success": False,
            "error": "Unsupported language. Choose en, hi, or kn.",
        }), 400

    # Change the active TTS language before committing the dashboard preference.
    active_controller = controller
    try:
        if active_controller is not None and getattr(active_controller, "tts", None) is not None:
            active_controller.tts.set_language(language)
        with language_lock:
            selected_speech_language = language
    except Exception as exc:
        system_logger.error(f"[LANGUAGE] Could not set speech language: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500

    system_logger.info(f"[LANGUAGE] Speech language set to {SUPPORTED_LANGUAGES[language]}")
    return jsonify({
        "success": True,
        "language": language,
        "label": SUPPORTED_LANGUAGES[language],
        "speech_engine_integrated": True,
        "message": f"Speech language set to {SUPPORTED_LANGUAGES[language]}.",
    })


# =========================================================
# APPLICATION STARTUP
# =========================================================

if __name__ == "__main__":

    print(
        "[WEB] Starting Smart AI Vision Assistant server..."
    )

    print(
        f"[WEB] Log source: {LOG_FILE}"
    )

    # -----------------------------------------------------
    # Start MainController in background.
    # -----------------------------------------------------

    threading.Thread(target=_telemetry_worker, name="ESP32TelemetryPoller", daemon=True).start()
    threading.Thread(target=_enrollment_worker_loop, name="FaceEnrollmentCapture", daemon=True).start()

    controller_thread = threading.Thread(
        target=start_controller,
        name="MainControllerThread",
        daemon=True,
    )

    controller_thread.start()

    print(
        "[WEB] MainController startup thread launched."
    )

    # -----------------------------------------------------
    # Start Flask.
    # -----------------------------------------------------

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
        threaded=True,
    )