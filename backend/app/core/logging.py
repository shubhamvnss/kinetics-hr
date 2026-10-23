import logging
import os

from logging.handlers import TimedRotatingFileHandler


LOG_DIR = "logs"

os.makedirs(LOG_DIR, exist_ok=True)

LOG_FORMAT = (
    "%(asctime)s | "
    "%(levelname)s | "
    "%(name)s | "
    "%(filename)s:%(lineno)d | "
    "%(message)s"
)

formatter = logging.Formatter(LOG_FORMAT)


def setup_logging():

    root_logger = logging.getLogger()

    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        root_logger.handlers.clear()

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Application Log
    app_handler = TimedRotatingFileHandler(
        filename=f"{LOG_DIR}/app.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )

    app_handler.setFormatter(formatter)

    # Error Log
    error_handler = TimedRotatingFileHandler(
        filename=f"{LOG_DIR}/error.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )

    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(app_handler)
    root_logger.addHandler(error_handler)


def get_logger(name: str):
    return logging.getLogger(name)