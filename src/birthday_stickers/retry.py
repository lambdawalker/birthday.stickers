"""Cooperative, unbounded retries for transient I/O failures."""

import errno
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_policy = ContextVar("retry_policy", default=(lambda: None, "images"))
log = logging.getLogger(__name__)


@contextmanager
def retry_scope(check=lambda: None, worker="images"):
    token = _policy.set((check, worker))
    try:
        yield
    finally:
        _policy.reset(token)


def pause(attempt, label, error):
    check, worker = _policy.get()
    delay = min(attempt, 15)
    check()
    log.warning("%s: %s; retry %s in %ss", label, error, attempt, delay, extra={"worker": worker})
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        check()
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    check()


def wait(seconds, checkpoint=lambda: None):
    check, _ = _policy.get()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check()
        checkpoint()
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    check()
    checkpoint()


def transient_file(error):
    return isinstance(error, OSError) and (
        error.errno
        in {
            errno.EACCES,
            errno.EPERM,
            errno.EBUSY,
            errno.EAGAIN,
            errno.EINTR,
            errno.ETIMEDOUT,
            errno.EIO,
        }
        or getattr(error, "winerror", None) in {5, 32, 33, 64, 121}
    )


def file_retry(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        attempt = 0
        while True:
            try:
                return function(*args, **kwargs)
            except OSError as error:
                if not transient_file(error):
                    raise
                attempt += 1
                pause(attempt, function.__name__, error)

    return wrapped


@file_retry
def read_bytes(path):
    return path.read_bytes()


@file_retry
def read_text(path, **kwargs):
    return path.read_text(**kwargs)


@file_retry
def copyfile(source, destination):
    import shutil

    return shutil.copyfile(source, destination)


@file_retry
def write_text(path, text, **kwargs):
    return path.write_text(text, **kwargs)


@file_retry
def replace(source, destination):
    import os

    return os.replace(source, destination)


@contextmanager
def file_lock(path):
    """Crash-released process lock for shared character conversions."""
    handle = file_retry(path.open)("a+b")
    try:
        if handle.seek(0, 2) == 0:
            file_retry(handle.write)(b"0")
            file_retry(handle.flush)()
        handle.seek(0)
        attempt = 0
        while True:
            try:
                if __import__("os").name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if not transient_file(error):
                    raise
                attempt += 1
                pause(attempt, "Waiting for character conversion lock", error)
        yield
    finally:
        handle.close()
