#include <Arduino.h>

#include <WiFi.h>

#include "esp_camera.h"

#include "img_converters.h"

#include "esp_http_server.h"

#include <string.h>

#include <stdio.h>

#include <stdlib.h>



// =====================================================

// ESP32-CAM RHYX M21-45 CLONE

// CONTINUOUS MJPEG STREAM + DISTANCE ALERTS

// =====================================================



// Wi-Fi configuration

const char* WIFI_SSID = "YOUR_WIFI_SSID";

const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";



// =====================================================

// AI THINKER ESP32-CAM PIN CONFIGURATION

// =====================================================



#define CAM_PIN_PWDN     32

#define CAM_PIN_RESET    -1

#define CAM_PIN_XCLK      0



#define CAM_PIN_SIOD     26

#define CAM_PIN_SIOC     27



#define CAM_PIN_Y9       35

#define CAM_PIN_Y8       34

#define CAM_PIN_Y7       39

#define CAM_PIN_Y6       36

#define CAM_PIN_Y5       21

#define CAM_PIN_Y4       19

#define CAM_PIN_Y3       18

#define CAM_PIN_Y2        5



#define CAM_PIN_VSYNC    25

#define CAM_PIN_HREF     23

#define CAM_PIN_PCLK     22



// =====================================================

// STREAM SETTINGS

// =====================================================



// Keep the XCLK setting that worked in previous tests.

static const int CAMERA_XCLK_HZ = 10000000;



// JPEG quality: higher values generally preserve more

// detail but create larger files.

static const int STREAM_JPEG_QUALITY = 50;



static const char* STREAM_CONTENT_TYPE =

    "multipart/x-mixed-replace;boundary=frame";



static const char* STREAM_BOUNDARY =

    "\r\n--frame\r\n";



static const char* STREAM_PART_HEADER =

    "Content-Type: image/jpeg\r\n"

    "Content-Length: %u\r\n\r\n";



static httpd_handle_t camera_httpd = nullptr;
static httpd_handle_t telemetry_httpd = nullptr;



bool cameraReady = false;

// HC-SR04 + passive buzzer.
// TRIG=GPIO13, ECHO=GPIO14 through a voltage divider, buzzer signal=GPIO15.
// GPIO15 is a boot-strapping pin: if boot becomes unreliable, disconnect its signal.
static const int US_TRIG_PIN = 13;
static const int US_ECHO_PIN = 14;
static const int BUZZER_PIN = 15;
static const unsigned long US_INTERVAL_MS = 100;
static const unsigned long US_TIMEOUT_US = 25000;
static float distanceCm = -1.0f;
static bool distanceValid = false;
static bool sensorReady = false;
static bool buzzerOn = false;

// One-shot GPS fix storage. Coordinates are accepted only when explicitly
// supplied to GET /gps?lat=...&lon=... (optional &accuracy=...).
static bool gpsFixValid = false;
static double gpsLatitude = 0.0;
static double gpsLongitude = 0.0;
static float gpsAccuracyM = -1.0f;
static unsigned long gpsReceivedAtMs = 0;
static unsigned long lastMeasureMs = 0;
static unsigned long lastLogMs = 0;

// Proximity alert patterns. An alert session ends after 3 seconds of continuous
// detected obstacle range; alerts stay suppressed until the area clears (>100 cm).
static bool alertSessionActive = false;
static bool alertSessionSuppressed = false;
static unsigned long alertSessionStartMs = 0;
static unsigned long lastBeepChangeMs = 0;
static bool proximityToneOn = false;
static int currentToneHz = 0;
static unsigned long currentBeepOnMs = 100;
static unsigned long currentBeepOffMs = 600;

// Non-blocking startup tones; proximity alerts wait until this sequence finishes.
static bool startupSequenceActive = false;
static int startupStep = 0;
static unsigned long startupNextMs = 0;
static bool startupToneOn = false;

static void setBuzzerTone(int frequencyHz) {
  if (frequencyHz <= 0) {
    noTone(BUZZER_PIN);
    digitalWrite(BUZZER_PIN, LOW);
    buzzerOn = false;
    currentToneHz = 0;
  } else {
    tone(BUZZER_PIN, frequencyHz);
    buzzerOn = true;
    currentToneHz = frequencyHz;
  }
}

static void playWifiBeepSequence() {
  // Three short beeps immediately before the Wi-Fi connection attempt.
  startupSequenceActive = false;
  setBuzzerTone(0);
  for (int i = 0; i < 3; ++i) {
    setBuzzerTone(2200);
    delay(100);
    setBuzzerTone(0);
    delay(130);
  }
}

static void beginReadyBeepSequence() {
  // Two longer beeps after Wi-Fi and HTTP server are ready.
  startupSequenceActive = true;
  startupStep = 100; // ready sequence marker
  startupToneOn = false;
  startupNextMs = millis();
}

static void updateStartupBeeps() {
  if (!startupSequenceActive) return;
  unsigned long now = millis();
  if (now < startupNextMs) return;

  if (startupStep < 100) {
    // 3 x (120 ms on, 140 ms off), then finish.
    if (!startupToneOn) {
      if (startupStep >= 3) { startupSequenceActive = false; setBuzzerTone(0); return; }
      setBuzzerTone(2200);
      startupToneOn = true;
      startupNextMs = now + 120;
    } else {
      setBuzzerTone(0);
      startupToneOn = false;
      startupStep++;
      startupNextMs = now + 140;
    }
  } else {
    // 2 x (300 ms on, 220 ms off), then finish.
    int substep = startupStep - 100;
    if (!startupToneOn) {
      if (substep >= 2) { startupSequenceActive = false; setBuzzerTone(0); return; }
      setBuzzerTone(2000);
      startupToneOn = true;
      startupNextMs = now + 300;
    } else {
      setBuzzerTone(0);
      startupToneOn = false;
      startupStep++;
      startupNextMs = now + 220;
    }
  }
}

static void updateUltrasonic() {
  unsigned long now = millis();
  if (now - lastMeasureMs < US_INTERVAL_MS) return;
  lastMeasureMs = now;
  digitalWrite(US_TRIG_PIN, LOW); delayMicroseconds(2);
  digitalWrite(US_TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(US_TRIG_PIN, LOW);
  unsigned long duration = pulseIn(US_ECHO_PIN, HIGH, US_TIMEOUT_US);
  if (duration == 0) { distanceValid = false; distanceCm = -1.0f; return; }
  float cm = duration * 0.0343f / 2.0f;
  if (cm < 2.0f || cm > 400.0f) { distanceValid = false; distanceCm = -1.0f; return; }
  distanceCm = cm; distanceValid = true; sensorReady = true;
}

static void updateProximityAlerts() {
  unsigned long now = millis();
  if (startupSequenceActive) return;

  // A valid clear reading ends the session and re-arms the next obstacle alert.
  if (distanceValid && distanceCm > 100.0f) {
    alertSessionActive = false;
    alertSessionSuppressed = false;
    proximityToneOn = false;
    setBuzzerTone(0);
    return;
  }
  if (!distanceValid) {
    // Keep the session timer if a single echo is missed, but silence while invalid.
    proximityToneOn = false;
    setBuzzerTone(0);
    return;
  }

  if (!alertSessionActive) {
    alertSessionActive = true;
    alertSessionSuppressed = false;
    alertSessionStartMs = now;
    proximityToneOn = false;
    lastBeepChangeMs = now;
  }
  if (now - alertSessionStartMs >= 3000UL) {
    alertSessionSuppressed = true;
    proximityToneOn = false;
    setBuzzerTone(0);
    return;
  }
  if (alertSessionSuppressed) { setBuzzerTone(0); return; }

  int frequency;
  if (distanceCm < 30.0f) {
    frequency = 3000; currentBeepOnMs = 50; currentBeepOffMs = 50;
  } else if (distanceCm < 60.0f) {
    frequency = 2500; currentBeepOnMs = 100; currentBeepOffMs = 200;
  } else { // 60..100 cm
    frequency = 2000; currentBeepOnMs = 120; currentBeepOffMs = 600;
  }

  unsigned long phaseDuration = proximityToneOn ? currentBeepOnMs : currentBeepOffMs;
  if (now - lastBeepChangeMs >= phaseDuration) {
    lastBeepChangeMs = now;
    proximityToneOn = !proximityToneOn;
    setBuzzerTone(proximityToneOn ? frequency : 0);
  } else if (proximityToneOn && currentToneHz != frequency) {
    setBuzzerTone(frequency);
  }
}

// =====================================================

// WEBPAGE

// =====================================================



static const char INDEX_HTML[] PROGMEM = R"HTML(

<!DOCTYPE html>

<html>

<head>

  <meta charset="UTF-8">



  <meta name="viewport"

        content="width=device-width, initial-scale=1.0">



  <title>ESP32-CAM Live Stream</title>



  <style>

    body {

      margin: 0;

      padding: 16px;

      background: #111;

      color: #eee;

      font-family: Arial, sans-serif;

      text-align: center;

    }



    h2 {

      margin-bottom: 8px;

    }



    p {

      color: #bbb;

      font-size: 14px;

    }



    #camera {

      display: block;

      width: 100%;

      max-width: 640px;

      height: auto;

      margin: 18px auto;

      background: #222;

      border: 1px solid #555;

    }



    button {

      padding: 11px 18px;

      margin: 5px;

      border: 0;

      border-radius: 5px;

      cursor: pointer;

      font-size: 15px;

    }



    #status {

      margin-top: 12px;

      color: #8f8;

    }

  </style>

</head>



<body>



  <h2>ESP32-CAM Live Stream</h2>



  <p>RHYX M21-45 | 320 × 240 | MJPEG</p>



  <img id="camera" alt="Connecting to camera...">



  <div>

    <button onclick="startStream()">Start stream</button>

    <button onclick="stopStream()">Stop stream</button>

  </div>



  <div id="status">Connecting...</div>



  <script>

    const camera = document.getElementById("camera");

    const statusText = document.getElementById("status");



    function startStream() {

      camera.src = "/stream?t=" + Date.now();

      statusText.textContent = "Live stream starting...";

    }



    function stopStream() {

      camera.removeAttribute("src");

      statusText.textContent = "Stream stopped.";

    }



    camera.onload = function() {

      statusText.textContent = "Receiving camera frames.";

    };



    camera.onerror = function() {

      statusText.textContent =

        "Stream disconnected. Press Start stream to retry.";

    };



    startStream();

  </script>



</body>

</html>

)HTML";



// =====================================================

// ROOT PAGE HANDLER

// =====================================================



static esp_err_t rootHandler(httpd_req_t* request) {



  httpd_resp_set_type(request, "text/html");



  return httpd_resp_send(

      request,

      INDEX_HTML,

      HTTPD_RESP_USE_STRLEN

  );

}



// =====================================================

// MJPEG STREAM HANDLER

// =====================================================



static esp_err_t streamHandler(httpd_req_t* request) {



  // Compatibility fix: use a custom HTTP status string.

  if (!cameraReady) {



    httpd_resp_set_status(

        request,

        "503 Service Unavailable"

    );



    httpd_resp_set_type(

        request,

        "text/plain"

    );



    httpd_resp_send(

        request,

        "Camera not ready",

        HTTPD_RESP_USE_STRLEN

    );



    return ESP_FAIL;

  }



  esp_err_t result = httpd_resp_set_type(

      request,

      STREAM_CONTENT_TYPE

  );



  if (result != ESP_OK) {

    return result;

  }



  httpd_resp_set_hdr(

      request,

      "Cache-Control",

      "no-cache, no-store, must-revalidate"

  );



  httpd_resp_set_hdr(

      request,

      "Access-Control-Allow-Origin",

      "*"

  );



  Serial.println("Live stream client connected.");



  unsigned long frameCounter = 0;

  unsigned long lastReport = millis();

  uint64_t captureTimeUs = 0;

  uint64_t convertTimeUs = 0;

  uint64_t sendTimeUs = 0;

  uint64_t totalJpegBytes = 0;



  while (true) {



    // -----------------------------------------------

    // Capture camera frame and measure capture time

    // -----------------------------------------------



    uint32_t captureStartUs = micros();

    camera_fb_t* frame = esp_camera_fb_get();

    uint32_t captureElapsedUs = micros() - captureStartUs;



    if (frame == nullptr) {

      Serial.println("ERROR: Camera frame capture failed.");

      result = ESP_FAIL;

      break;

    }



    // -----------------------------------------------

    // Convert RGB565 frame to JPEG and measure time

    // -----------------------------------------------



    uint8_t* jpegBuffer = nullptr;

    size_t jpegLength = 0;



    uint32_t convertStartUs = micros();

    bool converted = frame2jpg(

        frame,

        STREAM_JPEG_QUALITY,

        &jpegBuffer,

        &jpegLength

    );

    uint32_t convertElapsedUs = micros() - convertStartUs;



    // Return the camera frame after conversion is complete.

    esp_camera_fb_return(frame);



    if (!converted || jpegBuffer == nullptr || jpegLength == 0) {

      Serial.println("ERROR: JPEG conversion failed.");

      if (jpegBuffer != nullptr) {

        free(jpegBuffer);

      }

      result = ESP_FAIL;

      break;

    }



    // -----------------------------------------------

    // Send the complete MJPEG part and measure send time

    // -----------------------------------------------



    uint32_t sendStartUs = micros();



    // Combine the multipart boundary and JPEG headers into one small

    // chunk, reducing the number of HTTP send calls from three to two.

    char partPrefix[160];

    int prefixLength = snprintf(

        partPrefix,

        sizeof(partPrefix),

        "%sContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",

        STREAM_BOUNDARY,

        (unsigned int)jpegLength

    );



    if (prefixLength < 0 || prefixLength >= (int)sizeof(partPrefix)) {

      Serial.println("ERROR: MJPEG part header formatting failed.");

      free(jpegBuffer);

      result = ESP_FAIL;

      break;

    }



    result = httpd_resp_send_chunk(request, partPrefix, prefixLength);



    if (result != ESP_OK) {

      free(jpegBuffer);

      break;

    }



    result = httpd_resp_send_chunk(

        request,

        reinterpret_cast<const char*>(jpegBuffer),

        jpegLength

    );



    uint32_t sendElapsedUs = micros() - sendStartUs;

    totalJpegBytes += jpegLength;

    free(jpegBuffer);



    if (result != ESP_OK) {

      break;

    }



    frameCounter++;

    captureTimeUs += captureElapsedUs;

    convertTimeUs += convertElapsedUs;

    sendTimeUs += sendElapsedUs;



    // -----------------------------------------------

    // Report measured bottlenecks every \~5 seconds

    // -----------------------------------------------



    unsigned long now = millis();

    unsigned long reportElapsedMs = now - lastReport;



    if (reportElapsedMs >= 5000) {

      float actualFps = frameCounter * 1000.0f / reportElapsedMs;

      float avgCaptureMs = frameCounter ? captureTimeUs / 1000.0f / frameCounter : 0.0f;

      float avgConvertMs = frameCounter ? convertTimeUs / 1000.0f / frameCounter : 0.0f;

      float avgSendMs = frameCounter ? sendTimeUs / 1000.0f / frameCounter : 0.0f;

      float avgJpegKB = frameCounter ? totalJpegBytes / 1024.0f / frameCounter : 0.0f;



      Serial.println("========== STREAM PERFORMANCE ==========");

      Serial.printf("Frames sent: %lu in %.2f seconds\n", frameCounter, reportElapsedMs / 1000.0f);

      Serial.printf("Measured stream FPS: %.2f\n", actualFps);

      Serial.printf("Average capture time: %.2f ms/frame\n", avgCaptureMs);

      Serial.printf("Average JPEG conversion time: %.2f ms/frame\n", avgConvertMs);

      Serial.printf("Average HTTP send time: %.2f ms/frame\n", avgSendMs);

      Serial.printf("Average JPEG size: %.2f KB/frame\n", avgJpegKB);

      Serial.println("========================================");



      frameCounter = 0;

      captureTimeUs = 0;

      convertTimeUs = 0;

      sendTimeUs = 0;

      totalJpegBytes = 0;

      lastReport = now;

    }



    delay(0);

  }



  // End the HTTP response if the connection permits it.

  httpd_resp_send_chunk(request, nullptr, 0);



  Serial.println("Live stream client disconnected.");



  return ESP_OK;

}



// =====================================================

// START HTTP SERVER

// =====================================================



static esp_err_t distanceHandler(httpd_req_t* request) {
  char json[200];
  const char* zone = "unknown";
  if (distanceValid) {
    if (distanceCm < 30.0f) zone = "critical";
    else if (distanceCm < 60.0f) zone = "warning";
    else if (distanceCm <= 100.0f) zone = "caution";
    else zone = "clear";
  }
  snprintf(json, sizeof(json),
    "{\"distance_cm\":%.1f,\"valid\":%s,\"zone\":\"%s\",\"buzzer\":%s,\"sensor_ready\":%s}",
    distanceValid ? distanceCm : -1.0f, distanceValid ? "true" : "false", zone,
    buzzerOn ? "true" : "false", sensorReady ? "true" : "false");
  httpd_resp_set_type(request, "application/json");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  return httpd_resp_send(request, json, HTTPD_RESP_USE_STRLEN);
}

// GET /gps returns the last stored fix. To store a one-time fix, call:
// /gps?lat=12.345678&lon=76.543210&accuracy=15.0
// This endpoint does not poll the phone or request continuous updates.
static esp_err_t gpsHandler(httpd_req_t* request) {
  char query[160] = {0};
  if (httpd_req_get_url_query_str(request, query, sizeof(query)) == ESP_OK) {
    char latText[32] = {0};
    char lonText[32] = {0};
    char accuracyText[32] = {0};
    bool hasLat = httpd_query_key_value(query, "lat", latText, sizeof(latText)) == ESP_OK;
    bool hasLon = httpd_query_key_value(query, "lon", lonText, sizeof(lonText)) == ESP_OK;
    bool hasAccuracy = httpd_query_key_value(query, "accuracy", accuracyText, sizeof(accuracyText)) == ESP_OK;

    // Require latitude and longitude together; a bare GET only reads the fix.
    if (hasLat || hasLon || hasAccuracy) {
      if (!hasLat || !hasLon || latText[0] == '\0' || lonText[0] == '\0') {
        httpd_resp_set_status(request, "400 Bad Request");
        httpd_resp_set_type(request, "application/json");
        return httpd_resp_sendstr(request, "{\"error\":\"Provide both lat and lon; accuracy is optional.\"}");
      }
      char* latEnd = nullptr;
      char* lonEnd = nullptr;
      double lat = strtod(latText, &latEnd);
      double lon = strtod(lonText, &lonEnd);
      if (latEnd == latText || *latEnd != '\0' || lonEnd == lonText || *lonEnd != '\0' ||
          lat < -90.0 || lat > 90.0 || lon < -180.0 || lon > 180.0) {
        httpd_resp_set_status(request, "400 Bad Request");
        httpd_resp_set_type(request, "application/json");
        return httpd_resp_sendstr(request, "{\"error\":\"Invalid latitude or longitude.\"}");
      }
      float accuracy = -1.0f;
      if (hasAccuracy) {
        char* accuracyEnd = nullptr;
        accuracy = strtof(accuracyText, &accuracyEnd);
        if (accuracyEnd == accuracyText || *accuracyEnd != '\0' || accuracy < 0.0f || accuracy > 100000.0f) {
          httpd_resp_set_status(request, "400 Bad Request");
          httpd_resp_set_type(request, "application/json");
          return httpd_resp_sendstr(request, "{\"error\":\"Invalid accuracy.\"}");
        }
      }
      gpsLatitude = lat;
      gpsLongitude = lon;
      gpsAccuracyM = accuracy;
      gpsReceivedAtMs = millis();
      gpsFixValid = true;
      Serial.printf("One-shot GPS fix stored: lat=%.6f lon=%.6f accuracy=%.1f m\n", gpsLatitude, gpsLongitude, gpsAccuracyM);
    }
  }

  char json[240];
  if (gpsFixValid) {
    snprintf(json, sizeof(json),
      "{\"valid\":true,\"latitude\":%.6f,\"longitude\":%.6f,\"accuracy_m\":%.1f,\"age_ms\":%lu}",
      gpsLatitude, gpsLongitude, gpsAccuracyM, (unsigned long)(millis() - gpsReceivedAtMs));
  } else {
    snprintf(json, sizeof(json), "{\"valid\":false,\"message\":\"No GPS fix received yet.\"}");
  }
  httpd_resp_set_type(request, "application/json");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  return httpd_resp_send(request, json, HTTPD_RESP_USE_STRLEN);
}

bool startCameraServer() {



  httpd_config_t serverConfig = HTTPD_DEFAULT_CONFIG();



  serverConfig.server_port = 80;

  serverConfig.ctrl_port = 32768;

  serverConfig.max_uri_handlers = 8;

  serverConfig.stack_size = 8192;

  serverConfig.lru_purge_enable = true;



  if (httpd_start(

          &camera_httpd,

          &serverConfig

      ) != ESP_OK) {



    Serial.println("ERROR: HTTP server startup failed.");



    camera_httpd = nullptr;



    return false;

  }



  // -----------------------------------------------

  // Register homepage

  // -----------------------------------------------



  httpd_uri_t rootUri = {};



  rootUri.uri = "/";

  rootUri.method = HTTP_GET;

  rootUri.handler = rootHandler;

  rootUri.user_ctx = nullptr;



  if (httpd_register_uri_handler(

          camera_httpd,

          &rootUri

      ) != ESP_OK) {



    Serial.println("ERROR: Could not register homepage.");



    return false;

  }



  // -----------------------------------------------

  // Register live stream

  // -----------------------------------------------



  httpd_uri_t streamUri = {};



  streamUri.uri = "/stream";

  streamUri.method = HTTP_GET;

  streamUri.handler = streamHandler;

  streamUri.user_ctx = nullptr;



  if (httpd_register_uri_handler(

          camera_httpd,

          &streamUri

      ) != ESP_OK) {



    Serial.println("ERROR: Could not register stream.");



    return false;

  }



  // Keep sensor/GPS requests on a separate HTTP server. The MJPEG handler
  // remains open for the lifetime of the stream and can occupy the camera
  // server's request task. A second server keeps telemetry responsive.
  httpd_config_t telemetryConfig = HTTPD_DEFAULT_CONFIG();
  telemetryConfig.server_port = 81;
  telemetryConfig.ctrl_port = 32769;
  telemetryConfig.max_uri_handlers = 4;
  telemetryConfig.stack_size = 6144;
  telemetryConfig.lru_purge_enable = true;

  if (httpd_start(&telemetry_httpd, &telemetryConfig) != ESP_OK) {
    Serial.println("ERROR: Telemetry HTTP server startup failed on port 81.");
    telemetry_httpd = nullptr;
    return false;
  }

  httpd_uri_t gpsUri = {};
  gpsUri.uri = "/gps";
  gpsUri.method = HTTP_GET;
  gpsUri.handler = gpsHandler;
  gpsUri.user_ctx = nullptr;
  if (httpd_register_uri_handler(telemetry_httpd, &gpsUri) != ESP_OK) {
    Serial.println("ERROR: Could not register GPS endpoint on port 81.");
    return false;
  }

  httpd_uri_t distanceUri = {};
  distanceUri.uri = "/distance";
  distanceUri.method = HTTP_GET;
  distanceUri.handler = distanceHandler;
  distanceUri.user_ctx = nullptr;
  if (httpd_register_uri_handler(telemetry_httpd, &distanceUri) != ESP_OK) {
    Serial.println("ERROR: Could not register distance endpoint on port 81.");
    return false;
  }

  Serial.println("Camera HTTP server started on port 80.");
  Serial.println("Telemetry/GPS HTTP server started on port 81.");
  return true;

}



// =====================================================

// CAMERA INITIALIZATION

// =====================================================



bool initializeCamera() {



  camera_config_t config = {};



  config.ledc_channel = LEDC_CHANNEL_0;

  config.ledc_timer = LEDC_TIMER_0;



  config.pin_d0 = CAM_PIN_Y2;

  config.pin_d1 = CAM_PIN_Y3;

  config.pin_d2 = CAM_PIN_Y4;

  config.pin_d3 = CAM_PIN_Y5;

  config.pin_d4 = CAM_PIN_Y6;

  config.pin_d5 = CAM_PIN_Y7;

  config.pin_d6 = CAM_PIN_Y8;

  config.pin_d7 = CAM_PIN_Y9;



  config.pin_xclk = CAM_PIN_XCLK;

  config.pin_pclk = CAM_PIN_PCLK;



  config.pin_vsync = CAM_PIN_VSYNC;

  config.pin_href = CAM_PIN_HREF;



  config.pin_sccb_sda = CAM_PIN_SIOD;

  config.pin_sccb_scl = CAM_PIN_SIOC;



  config.pin_pwdn = CAM_PIN_PWDN;

  config.pin_reset = CAM_PIN_RESET;



  config.xclk_freq_hz = CAMERA_XCLK_HZ;



  // Preserve the capture format used successfully before.

  config.pixel_format = PIXFORMAT_RGB565;

  config.frame_size = FRAMESIZE_QVGA;



  config.jpeg_quality = 12;



  // One frame buffer for the initial stability test.

  config.fb_count = 1;



  config.fb_location = CAMERA_FB_IN_PSRAM;

  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;



  Serial.println();

  Serial.println("Initializing RHYX M21-45 camera...");



  if (psramFound()) {

    Serial.println("PSRAM detected.");

  } else {

    Serial.println("WARNING: PSRAM not detected.");

  }



  esp_err_t error = esp_camera_init(&config);



  if (error != ESP_OK) {



    Serial.printf(

        "Camera initialization failed: 0x%x\n",

        error

    );



    return false;

  }



  sensor_t* sensor = esp_camera_sensor_get();



  if (sensor != nullptr) {



    Serial.printf(

        "Sensor PID: 0x%04X\n",

        sensor->id.PID

    );



    // Keep automatic exposure and gain enabled.


  }



  Serial.println("Camera initialized successfully.");



  return true;

}



// =====================================================

// WIFI CONNECTION

// =====================================================



bool connectWiFi() {



  Serial.println();

  Serial.println("Connecting to phone hotspot...");
  playWifiBeepSequence();



  WiFi.mode(WIFI_STA);



  // Prevent Wi-Fi sleep from interfering with streaming.

  WiFi.setSleep(false);



  WiFi.begin(

      WIFI_SSID,

      WIFI_PASSWORD

  );



  unsigned long startTime = millis();



  while (

      WiFi.status() != WL_CONNECTED &&

      millis() - startTime < 30000

  ) {



    Serial.print(".");

    delay(500);

  }



  Serial.println();



  if (WiFi.status() != WL_CONNECTED) {



    Serial.println("ERROR: Wi-Fi connection failed.");



    return false;

  }



  Serial.println("Wi-Fi connected.");



  Serial.print("ESP32 IP address: ");

  Serial.println(WiFi.localIP());



  Serial.print("Wi-Fi signal strength: ");

  Serial.print(WiFi.RSSI());

  Serial.println(" dBm");



  return true;

}



// =====================================================

// SETUP

// =====================================================



void setup() {



  Serial.begin(115200);
  pinMode(US_TRIG_PIN, OUTPUT);
  pinMode(US_ECHO_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(US_TRIG_PIN, LOW);
  setBuzzerTone(0);



  delay(1000);



  Serial.println();

  Serial.println("======================================");

  Serial.println("ESP32-CAM CONTINUOUS MJPEG STREAM");

  Serial.println("RHYX M21-45 CLONE");

  Serial.println("======================================");





  cameraReady = initializeCamera();



  if (!cameraReady) {



    Serial.println("Camera startup failed.");



    return;

  }



  if (!connectWiFi()) {



    Serial.println("Wi-Fi startup failed.");



    return;

  }



  if (!startCameraServer()) {



    Serial.println("HTTP server startup failed.");



    return;

  }



  Serial.println();

  Serial.println("======================================");

  Serial.println("CAMERA READY");



  Serial.print("Homepage: http\://");

  Serial.println(WiFi.localIP());



  Serial.print("Live stream: http\://");

  Serial.print(WiFi.localIP());

  Serial.println("/stream");
  Serial.print("Distance API: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":81/distance");
  Serial.print("GPS API: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":81/gps");

  Serial.println("======================================");
  beginReadyBeepSequence();

}



// =====================================================

// MAIN LOOP

// =====================================================



void loop() {
  updateUltrasonic();
  updateStartupBeeps();
  updateProximityAlerts();

  unsigned long now = millis();
  if (now - lastLogMs >= 1000UL) {
    lastLogMs = now;
    if (distanceValid) {
      const char* zone = distanceCm < 30.0f ? "critical" :
        distanceCm < 60.0f ? "warning" : distanceCm <= 100.0f ? "caution" : "clear";
      Serial.printf("Distance: %.1f cm | Zone: %s | Buzzer: %s\n",
        distanceCm, zone, buzzerOn ? "ON" : "OFF");
    } else Serial.println("Ultrasonic reading invalid/no echo.");
  }
  delay(1);
}
