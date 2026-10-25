import logging
import sys
from pathlib import Path

from system_event_bus import system_event_bus


class EventBusHandler(logging.Handler):

    def emit(self, record):
        try:
            message = record.getMessage()

            category = "SYSTEM"

            if message.startswith("[") and "]" in message:
                category = message[1:message.find("]")].upper()

            system_event_bus.publish(
                level=record.levelname,
                message=message,
                category=category
            )

        except Exception:
            pass


class SystemLogger:

    def __init__(self, log_file=None):
        project_root = Path(__file__).resolve().parent

        if log_file is None:
            log_file = project_root / "system.log"

        self.log_file = Path(log_file)

        self.logger = logging.getLogger(
            "SmartAIVisionAssistant"
        )

        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        if not self.logger.handlers:

            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S"
            )

            console_handler = logging.StreamHandler(
                sys.stdout
            )

            console_handler.setLevel(
                logging.DEBUG
            )

            console_handler.setFormatter(
                formatter
            )

            file_handler = logging.FileHandler(
                self.log_file,
                encoding="utf-8"
            )

            file_handler.setLevel(
                logging.DEBUG
            )

            file_handler.setFormatter(
                formatter
            )

            event_handler = EventBusHandler()
            event_handler.setLevel(
                logging.DEBUG
            )

            self.logger.addHandler(
                console_handler
            )

            self.logger.addHandler(
                file_handler
            )

            self.logger.addHandler(
                event_handler
            )

        print(
            "[LOGGER] System logger initialized."
        )

        print(
            f"[LOGGER] Log file: {self.log_file}"
        )

    def debug(self, message):
        self.logger.debug(message)

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)

    def critical(self, message):
        self.logger.critical(message)


system_logger = SystemLogger()
