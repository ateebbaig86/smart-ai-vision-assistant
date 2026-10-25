"""Windows native .NET System.Speech TTS manager.

Uses Windows' System.Speech.Synthesis.SpeechSynthesizer rather than pyttsx3/
SAPI.SpVoice. All speech-engine operations are coordinated by one worker.
Priority-1 announcements can cancel ordinary priority-2 narration.
"""

import heapq
import threading
import time
import os
import re
import tempfile
import wave
from pathlib import Path



class TTSManager:
    def __init__(self, rate=1, volume=100, stale_after=2.0, max_object_messages=2):
        self.base_dir = Path(__file__).resolve().parent
        self.language = "en"
        # Keep independent cached voices: Hindi and Kannada use different models.
        self._piper_voice = None
        self._piper_error = None
        self._kannada_piper_voice = None
        self._kannada_piper_error = None
        self._sherpa_tts = None
        self._sherpa_error = None
        self._language_lock = threading.Lock()
        self.rate = int(rate)
        self.volume = max(0, min(100, int(volume)))
        self.stale_after = max(0.5, float(stale_after))
        self.max_object_messages = max(1, int(max_object_messages))

        self._condition = threading.Condition()
        # Queue entries: (priority, sequence, text, queued_at)
        self._pending = []
        self._sequence = 0
        self._running = True
        self._speaking = False
        self._active_text = None
        self._engine = None
        self._speech_done = threading.Event()
        self._speech_done.set()
        self._speech_cancelled = False

        self._worker = threading.Thread(
            target=self._speech_worker,
            name="TTS-Speech-Worker",
            daemon=True,
        )
        self._worker.start()
        print("[TTS] Multilingual speech manager started (English Windows; Hindi and Kannada offline Piper when models are present).")

    def set_language(self, language):
        """Set speech language: en, hi, or kn. Existing queued messages use the language active when spoken."""
        language = str(language or "en").lower().strip()
        if language not in {"en", "hi", "kn"}:
            raise ValueError("Unsupported language. Choose en, hi, or kn.")
        with self._language_lock:
            self.language = language
        print(f"[TTS] Speech language set to {language}")
        return True

    def get_language(self):
        with self._language_lock:
            return self.language

    def _translate_for_language(self, text, language):
        """Translate common assistive-vision phrases locally; unknown phrases remain unchanged."""
        if language == "en":
            return text
        # The controller's standard event phrases are handled as phrase patterns.
        common = {
            "person": ("व्यक्ति", "ವ್ಯಕ್ತಿ"), "people": ("लोग", "ಜನರು"),
            "car": ("कार", "ಕಾರು"), "truck": ("ट्रक", "ಟ್ರಕ್"),
            "bus": ("बस", "ಬಸ್"), "bicycle": ("साइकिल", "ಬೈಸಿಕಲ್"),
            "motorcycle": ("मोटरसाइकिल", "ಮೋಟಾರ್ ಸೈಕಲ್"),
            "chair": ("कुर्सी", "ಕುರ್ಚಿ"), "table": ("मेज़", "ಮೇಜು"),
            "door": ("दरवाज़ा", "ಬಾಗಿಲು"), "stairs": ("सीढ़ियाँ", "ಮೆಟ್ಟಿಲುಗಳು"),
            "stair": ("सीढ़ी", "ಮೆಟ್ಟಿಲು"), "obstacle": ("रुकावट", "ಅಡಚಣೆ"),
            "left": ("बाईं ओर", "ಎಡಕ್ಕೆ"), "right": ("दाईं ओर", "ಬಲಕ್ಕೆ"),
            "ahead": ("आगे", "ಮುಂದೆ"), "behind": ("पीछे", "ಹಿಂದೆ"),
            "near": ("पास में", "ಹತ್ತಿರ"), "close": ("बहुत पास", "ತುಂಬಾ ಹತ್ತಿರ"),
            "warning": ("सावधान", "ಎಚ್ಚರಿಕೆ"), "stop": ("रुकिए", "ನಿಲ್ಲಿ"),
            "careful": ("सावधान रहें", "ಜಾಗರೂಕರಾಗಿರಿ"), "unknown person": ("अज्ञात व्यक्ति", "ಅಪರಿಚಿತ ವ್ಯಕ್ತಿ"),
            "recognized": ("पहचाना गया", "ಗುರುತಿಸಲಾಗಿದೆ"),
        }
        idx = 0 if language == "hi" else 1
        translated = text
        # Exact known safety phrases first.
        exact = {
            "hi": {
                "Obstacle ahead. Please be careful.": "आगे रास्ते में बाधा है। कृपया सावधानी से चलें।",
                "Obstacle detected ahead. Please be careful.": "आगे रास्ते में बाधा है। कृपया सावधानी से चलें।",
                "Stop. Obstacle very close.": "रुकिए। बाधा बहुत पास है।",
                "Person detected.": "व्यक्ति सामने है।",
                "Unknown person.": "अज्ञात व्यक्ति सामने है।",
            },
            "kn": {
                "Obstacle ahead. Please be careful.": "ಮುಂದೆ ಅಡ್ಡಿ ಇದೆ. ದಯವಿಟ್ಟು ಜಾಗರೂಕರಾಗಿ ನಡೆಯಿರಿ.",
                "Obstacle detected ahead. Please be careful.": "ಮುಂದೆ ಅಡ್ಡಿ ಇದೆ. ದಯವಿಟ್ಟು ಜಾಗರೂಕರಾಗಿ ನಡೆಯಿರಿ.",
                "Stop. Obstacle very close.": "ನಿಲ್ಲಿ. ಅಡ್ಡಿ ತುಂಬಾ ಹತ್ತಿರದಲ್ಲಿದೆ.",
                "Person detected.": "ಮುಂದೆ ವ್ಯಕ್ತಿ ಇದ್ದಾರೆ.",
                "Unknown person.": "ಅಪರಿಚಿತ ವ್ಯಕ್ತಿ ಮುಂದೆ ಇದ್ದಾರೆ.",
                "Please stop. An obstacle is very close.": "ದಯವಿಟ್ಟು ನಿಲ್ಲಿ. ಅಡ್ಡಿ ತುಂಬಾ ಹತ್ತಿರದಲ್ಲಿದೆ.",
                "Obstacle detected on your left.": "ನಿಮ್ಮ ಎಡಭಾಗದಲ್ಲಿ ಅಡ್ಡಿ ಪತ್ತೆಯಾಗಿದೆ.",
                "Obstacle detected on your right.": "ನಿಮ್ಮ ಬಲಭಾಗದಲ್ಲಿ ಅಡ್ಡಿ ಪತ್ತೆಯಾಗಿದೆ.",
                "Person on your left.": "ನಿಮ್ಮ ಎಡಭಾಗದಲ್ಲಿ ವ್ಯಕ್ತಿ ಇದ್ದಾರೆ.",
                "Person on your right.": "ನಿಮ್ಮ ಬಲಭಾಗದಲ್ಲಿ ವ್ಯಕ್ತಿ ಇದ್ದಾರೆ.",
                "Path clear.": "ಮುಂದಿನ ದಾರಿ ಖಾಲಿಯಾಗಿದೆ.",
            },
        }
        if text in exact.get(language, {}):
            return exact[language][text]
        # Translate known vocabulary while preserving unknown class names/values.
        for english, values in sorted(common.items(), key=lambda item: len(item[0]), reverse=True):
            translated = re.sub(r"\b" + re.escape(english) + r"\b", values[idx], translated, flags=re.IGNORECASE)
        if language == "hi":
            translated = translated.replace("New ", "नया ").replace(" detected", " दिखाई दिया").replace(" is ", " है ")
        else:
            translated = translated.replace("New ", "ಹೊಸ ").replace(" detected", " ಪತ್ತೆಯಾಗಿದೆ").replace(" is ", " ಇದೆ ")
        return translated

    def _get_piper_voice(self):
        if self._piper_voice is not None:
            return self._piper_voice
        model = self.base_dir / "tts-test" / "hi_IN-pratham-medium.onnx"
        config = self.base_dir / "indic-tts-main" / "models" / "hi_IN-pratham-medium.onnx.json"
        if not model.is_file() or not config.is_file():
            raise FileNotFoundError(f"Hindi Piper model/config not found: {model} ; {config}")
        from piper import PiperVoice
        self._piper_voice = PiperVoice.load(str(model), config_path=str(config), use_cuda=False)
        return self._piper_voice

    def _get_kannada_piper_voice(self):
        """Load the project-local Kannada Piper ONNX model on first use."""
        if self._kannada_piper_voice is not None:
            return self._kannada_piper_voice
        model = self.base_dir / "tts-test" / "kn_IN-syspin_male-medium.onnx"
        config = self.base_dir / "tts-test" / "kn_IN-syspin_male-medium.onnx.json"
        if not model.is_file() or not config.is_file():
            raise FileNotFoundError(
                f"Kannada Piper model/config not found: {model} ; {config}"
            )
        from piper import PiperVoice
        self._kannada_piper_voice = PiperVoice.load(
            str(model), config_path=str(config), use_cuda=False
        )
        print("[TTS] Kannada offline Piper voice loaded successfully.")
        return self._kannada_piper_voice

    def _play_piper(self, text, priority, language="hi"):
        import winsound
        voice = self._get_kannada_piper_voice() if language == "kn" else self._get_piper_voice()
        fd, wav_path = tempfile.mkstemp(prefix="vision_tts_", suffix=".wav")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            while True:
                with self._condition:
                    running = self._running
                if not running:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    return True
                if priority > 1 and self._has_urgent_pending():
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    return True
                # PlaySound's async state isn't exposed; duration from WAV frames is reliable enough.
                with wave.open(wav_path, "rb") as wf:
                    duration = wf.getnframes() / float(wf.getframerate() or 22050)
                deadline = time.monotonic() + duration
                while time.monotonic() < deadline:
                    with self._condition:
                        if not self._running or (priority > 1 and any(x[0] == 1 for x in self._pending)):
                            winsound.PlaySound(None, winsound.SND_PURGE)
                            return True
                    time.sleep(0.03)
                return False
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

    def _play_sapi_text(self, text, priority):
        """Use the existing Windows System.Speech engine for English/Kannada fallback."""
        return self._speak_async_and_wait(text, priority)

    def speak(self, message, priority=2):
        text = str(message).strip() if message is not None else ""
        if not text:
            return False

        try:
            priority = 1 if int(priority) <= 1 else 2
        except (TypeError, ValueError):
            priority = 2

        now = time.monotonic()
        normalized = text.casefold()

        with self._condition:
            if not self._running:
                return False

            if (
                self._active_text
                and self._active_text.casefold() == normalized
            ) or any(item[2].casefold() == normalized for item in self._pending):
                return True

            self._sequence += 1
            entry = (priority, self._sequence, text, now)

            if priority == 2:
                urgent_items = [item for item in self._pending if item[0] == 1]
                normal_items = [item for item in self._pending if item[0] == 2]
                normal_items.append(entry)
                normal_items = normal_items[-self.max_object_messages:]
                self._pending = urgent_items + normal_items
                heapq.heapify(self._pending)
                print(f"[TTS] Object queued/coalesced: {text}")
            else:
                heapq.heappush(self._pending, entry)
                print(f"[TTS] Priority announcement queued: {text}")

            self._condition.notify_all()
        return True

    def speak_safety(self, message):
        return self.speak(message, priority=1)

    def speak_object(self, message):
        return self.speak(message, priority=2)

    def _create_engine(self):
        # Load .NET Framework before importing clr. The full assembly path is
        # important on this PC; AddReference("System.Speech") alone failed.
        from pythonnet import load
        load("netfx")

        import clr
        clr.AddReference(
            r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF\System.Speech.dll"
        )
        from System.Speech.Synthesis import SpeechSynthesizer

        engine = SpeechSynthesizer()
        engine.Volume = self.volume
        engine.Rate = max(-10, min(10, self.rate))

        try:
            voice = engine.Voice
            print(f"[TTS] Voice selected: {voice.Name}")
        except Exception as exc:
            print(f"[TTS] Voice listing unavailable: {exc}")

        return engine

    def _get_next(self):
        with self._condition:
            while self._running and not self._pending:
                self._condition.wait(timeout=0.25)
            if not self._running:
                return None

            # Allow detections from the same inference cycle to coalesce.
            self._condition.wait(timeout=0.06)
            if not self._running:
                return None

            now = time.monotonic()
            self._pending = [
                item for item in self._pending
                if item[0] == 1 or now - item[3] <= self.stale_after
            ]
            heapq.heapify(self._pending)
            if not self._pending:
                return False

            priority, sequence, text, queued_at = heapq.heappop(self._pending)

            if priority == 2:
                batch = [(priority, sequence, text, queued_at)]
                remaining = []
                while self._pending:
                    item = heapq.heappop(self._pending)
                    if item[0] == 2 and len(batch) < self.max_object_messages:
                        batch.append(item)
                    else:
                        remaining.append(item)
                for item in remaining:
                    heapq.heappush(self._pending, item)

                seen = set()
                parts = []
                for item in sorted(batch, key=lambda x: x[1]):
                    clean = item[2].strip().rstrip(".! ")
                    key = clean.casefold()
                    if clean and key not in seen:
                        seen.add(key)
                        parts.append(clean)
                text = ". ".join(parts) + "."
                sequence = batch[-1][1]
                queued_at = min(item[3] for item in batch)

            return priority, sequence, text, queued_at

    def _has_urgent_pending(self):
        with self._condition:
            return any(item[0] == 1 for item in self._pending)

    def _cancel_speech(self):
        engine = self._engine
        if engine is not None:
            try:
                engine.SpeakAsyncCancelAll()
            except Exception as exc:
                print(f"[TTS] Speech cancellation warning: {exc}")

    def _speak_async_and_wait(self, text, priority):
        """Speak asynchronously and wait for the real completion callback."""
        self._speech_done.clear()
        self._speech_cancelled = False

        def on_speak_completed(sender, event_args):
            try:
                self._speech_cancelled = bool(event_args.Cancelled)
            except Exception:
                self._speech_cancelled = False
            self._speech_done.set()

        handler = on_speak_completed
        self._engine.SpeakCompleted += handler

        try:
            self._engine.SpeakAsync(text)

            while not self._speech_done.wait(timeout=0.05):
                with self._condition:
                    running = self._running

                if not running:
                    self._cancel_speech()
                    self._speech_done.wait(timeout=1.0)
                    return True

                if priority > 1 and self._has_urgent_pending():
                    self._cancel_speech()
                    self._speech_done.wait(timeout=1.0)
                    return True

            return self._speech_cancelled
        finally:
            try:
                self._engine.SpeakCompleted -= handler
            except Exception:
                pass

    def _speech_worker(self):
        try:
            self._engine = self._create_engine()

            while True:
                item = self._get_next()
                if item is None:
                    break
                if item is False:
                    continue

                priority, sequence, text, queued_at = item
                start = time.monotonic()

                with self._condition:
                    self._speaking = True
                    self._active_text = text

                print(
                    f"[TTS] START priority={priority}, sequence={sequence}, "
                    f"queue_delay={start - queued_at:.2f}s: {text}"
                )

                interrupted = False
                try:
                    language = self.get_language()
                    spoken_text = self._translate_for_language(text, language)
                    if language in {"hi", "kn"}:
                        try:
                            interrupted = self._play_piper(
                                spoken_text, priority, language=language
                            )
                        except Exception as piper_error:
                            language_name = "Kannada" if language == "kn" else "Hindi"
                            print(
                                f"[TTS] Offline Piper {language} failed; "
                                f"using Windows speech fallback for {language_name}: {piper_error}"
                            )
                            interrupted = self._speak_async_and_wait(spoken_text, priority)
                    else:
                        interrupted = self._speak_async_and_wait(spoken_text, priority)
                    elapsed = time.monotonic() - start
                    label = "INTERRUPTED" if interrupted else "END"
                    print(
                        f"[TTS] {label} sequence={sequence}, "
                        f"elapsed={elapsed:.2f}s: {text}"
                    )
                except Exception as exc:
                    print(f"[TTS] SPEECH ERROR sequence={sequence}: {exc}")
                    try:
                        if self._engine is not None:
                            self._engine.Dispose()
                    except Exception:
                        pass
                    try:
                        self._engine = self._create_engine()
                    except Exception as recreate_exc:
                        print(f"[TTS] Engine recovery failed: {recreate_exc}")
                finally:
                    with self._condition:
                        self._speaking = False
                        self._active_text = None
                        self._condition.notify_all()

        except Exception as exc:
            print(f"[TTS] Worker initialization/runtime error: {exc}")
        finally:
            try:
                if self._engine is not None:
                    self._cancel_speech()
                    self._engine.Dispose()
            except Exception as exc:
                print(f"[TTS] Engine shutdown warning: {exc}")

            with self._condition:
                self._speaking = False
                self._active_text = None
                self._condition.notify_all()
            print("[TTS] Speech worker stopped.")

    def wait_until_done(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        with self._condition:
            while self._speaking or self._pending:
                if deadline is None:
                    self._condition.wait(timeout=0.1)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(timeout=min(0.1, remaining))
            return True

    def stop(self, wait=True, timeout=3):
        with self._condition:
            if not self._running:
                return
            self._running = False
            self._pending.clear()
            self._condition.notify_all()

        # Cancel immediately so shutdown does not wait for a long utterance.
        self._cancel_speech()

        if wait and threading.current_thread() is not self._worker:
            self._worker.join(timeout=max(0, timeout))
        print("[TTS] Manager stopped.")
