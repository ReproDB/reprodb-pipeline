"""Tests for src.utils.logging_config — logging setup helpers."""

import argparse
import json
import logging

from src.utils.io.logging_config import LOG_LEVELS, JSONFormatter, add_log_level_arg, setup_logging


class TestLogLevels:
    def test_maps_standard_levels(self):
        assert LOG_LEVELS["debug"] == logging.DEBUG
        assert LOG_LEVELS["info"] == logging.INFO
        assert LOG_LEVELS["warning"] == logging.WARNING
        assert LOG_LEVELS["error"] == logging.ERROR


class TestAddLogLevelArg:
    def test_adds_log_level_argument(self):
        parser = argparse.ArgumentParser()
        add_log_level_arg(parser)
        args = parser.parse_args(["--log-level", "debug"])
        assert args.log_level == "debug"

    def test_default_is_info(self):
        parser = argparse.ArgumentParser()
        add_log_level_arg(parser)
        args = parser.parse_args([])
        assert args.log_level == "info"

    def test_rejects_invalid_level(self):
        parser = argparse.ArgumentParser()
        add_log_level_arg(parser)
        import pytest

        with pytest.raises(SystemExit):
            parser.parse_args(["--log-level", "verbose"])

    def test_log_format_default_is_text(self):
        parser = argparse.ArgumentParser()
        add_log_level_arg(parser)
        args = parser.parse_args([])
        assert args.log_format == "text"

    def test_log_format_json(self):
        parser = argparse.ArgumentParser()
        add_log_level_arg(parser)
        args = parser.parse_args(["--log-format", "json"])
        assert args.log_format == "json"


class TestSetupLogging:
    def _reset_root_logger(self):
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.NOTSET)

    def test_accepts_int_level(self):
        self._reset_root_logger()
        setup_logging(logging.DEBUG)
        assert logging.root.level == logging.DEBUG

    def test_accepts_string_level(self):
        self._reset_root_logger()
        setup_logging("warning")
        assert logging.root.level == logging.WARNING

    def test_unknown_string_defaults_to_info(self):
        self._reset_root_logger()
        setup_logging("bogus")
        assert logging.root.level == logging.INFO

    def test_json_format_sets_json_formatter(self):
        self._reset_root_logger()
        setup_logging(logging.INFO, log_format="json")
        assert len(logging.root.handlers) == 1
        assert isinstance(logging.root.handlers[0].formatter, JSONFormatter)

    def test_text_format_is_default(self):
        self._reset_root_logger()
        setup_logging(logging.INFO)
        assert len(logging.root.handlers) == 1
        assert not isinstance(logging.root.handlers[0].formatter, JSONFormatter)

    def test_repeated_calls_dont_duplicate_handlers(self):
        self._reset_root_logger()
        setup_logging(logging.INFO)
        setup_logging(logging.DEBUG)
        assert len(logging.root.handlers) == 1


class TestJSONFormatter:
    def test_output_is_valid_json(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0, msg="hello %s", args=("world",), exc_info=None
        )
        line = fmt.format(record)
        data = json.loads(line)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert "ts" in data

    def test_exception_included(self):
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0, msg="fail", args=(), exc_info=exc_info
        )
        data = json.loads(fmt.format(record))
        assert "exc" in data
        assert "boom" in data["exc"]
