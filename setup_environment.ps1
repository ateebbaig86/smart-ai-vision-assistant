$ErrorActionPreference = "Stop"
$Project = $PSScriptRoot
Set-Location $Project

Write-Host "`n=== ACTIVE PYTHON INTERPRETER ===" -ForegroundColor Cyan

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "Python was not found. Select or activate your team's existing environment."
}

$Python = $PythonCommand.Source
& $Python -c "import sys,struct; print('Executable:',sys.executable); print('Version:',sys.version.split()[0]); print('64-bit:',struct.calcsize('P')*8 == 64); print('Virtual environment:',sys.prefix != sys.base_prefix); print('Environment path:',sys.prefix)"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to run the selected Python interpreter."
}

Write-Host "`n=== VERSION CHECK ===" -ForegroundColor Cyan
& $Python -c "import sys,struct; sys.exit(0 if sys.version_info[:2] == (3,11) and struct.calcsize('P')*8 == 64 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Expected Python 3.11, 64-bit. Select the correct existing interpreter."
}

Write-Host "`n=== REQUIRED IMPORTS ===" -ForegroundColor Cyan

$Check = @"
import importlib, importlib.metadata as md, sys

required = {
    "flask": "Flask",
    "cv2": "opencv-contrib-python",
    "numpy": "numpy",
    "ultralytics": "ultralytics",
    "torch": "torch",
    "torchvision": "torchvision",
    "insightface": "insightface",
    "onnxruntime": "onnxruntime",
    "piper": "piper-tts",
    "clr": "pythonnet",
    "requests": "requests",
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "mediapipe": "mediapipe",
    "sounddevice": "sounddevice",
    "soundfile": "soundfile",
}
failed = False
for module, distribution in required.items():
    try:
        importlib.import_module(module)
        try:
            version = md.version(distribution)
        except md.PackageNotFoundError:
            version = "import succeeded; distribution metadata unavailable"
        print("PASS", module, "-", version)
    except Exception as exc:
        failed = True
        print("FAIL", module, "-", type(exc).__name__, str(exc))
sys.exit(1 if failed else 0)
"@

$Check | & $Python -
if ($LASTEXITCODE -ne 0) {
    throw "Import checks failed. No packages were installed or changed. Select the correct existing environment and rerun."
}

Write-Host "`n=== REQUIRED PROJECT ASSETS ===" -ForegroundColor Cyan
$Assets = @(
    "web_server.py",
    "main_controller.py",
    "config.py",
    "camera_factory.py",
    "camera_source.py",
    "esp32_camera.py",
    "detection_state_manager.py",
    "proximity_manager.py",
    "face_recognition_manager.py",
    "face_enrollment_manager.py",
    "voice_event_manager.py",
    "tts_manager.py",
    "system_event_bus.py",
    "system_logger.py",
    "yolo_detector.py",
    "templates\index.html",
    "Unified_Model_30ep.pt",
    "tts-test\hi_IN-pratham-medium.onnx",
    "indic-tts-main\models\hi_IN-pratham-medium.onnx.json",
    "ESP32_CAM_SMART_AI_ASSISTANT_LIVE_TELEMETRY\ESP32_CAM_SMART_AI_ASSISTANT_LIVE_TELEMETRY.ino"
)

$Missing = @()
foreach ($Asset in $Assets) {
    if (Test-Path (Join-Path $Project $Asset)) {
        Write-Host "PASS $Asset" -ForegroundColor Green
    } else {
        Write-Host "MISSING $Asset" -ForegroundColor Red
        $Missing += $Asset
    }
}

if ($Missing.Count -gt 0) {
    throw "Required project assets are missing. Review the list above."
}

Write-Host "`nALL CHECKS PASSED." -ForegroundColor Green
Write-Host "No virtual environment was created and no packages were installed."
Write-Host "Launch using the verified interpreter: python .\web_server.py"
