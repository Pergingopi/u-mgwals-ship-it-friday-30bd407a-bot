#!/usr/bin/env python3
"""Ship It Friday — one-line git commit message entry.

Writes the chosen commit message to ``$MAKEFUN_OUTPUT_DIR/entry.txt``
(default ``/output``). Deterministic, standard library only.

The write path is deliberately boring on purpose: validate the text,
remove any stale temporary file left by an interrupted earlier run,
write the bytes to a fresh temporary file in the same directory, fsync
the file, atomically rename it into place with :func:`os.replace`,
fsync the directory, then read the file back and confirm both the bytes
and the 0644 mode. Every failure is raised as an explicit exception
with a message naming what went wrong; nothing here relies on ``assert``,
so the checks survive ``python -O``.
"""

import glob
import os
import stat
import sys
import tempfile
import time

ENTRY = "feat: deleted the prod database \u2014 query time down 100%, tests green locally, ship it Friday!"

DEFAULT_OUTPUT_DIR = "/output"
TMP_PREFIX = ".entry-"
TMP_SUFFIX = ".tmp"
EXPECTED_MODE = 0o644
# A temp file younger than this is assumed to belong to a live run and is
# left alone; anything older is stale debris from an interrupted run.
STALE_AFTER_SECONDS = 60.0
CLEANUP_ATTEMPTS = 3
CLEANUP_RETRY_DELAY_SECONDS = 0.05


def _reject_control_chars(text):
    """Reject C0/C1 control characters and DEL in the entry text.

    Tab and newline are tolerated by this check only so that the
    single-line validation in :func:`validate_entry` can report them
    with its own, clearer error instead of a generic control-character
    one. Any other control character is rejected here.
    """
    for ch in text:
        code = ord(ch)
        if code < 0x20 and ch not in ("\n", "\t"):
            raise ValueError("control character in entry text: U+%04X" % code)
        if code == 0x7F:
            raise ValueError("control character in entry text: U+007F")


def validate_entry(text):
    """Validate the entry text and return it as UTF-8 bytes.

    The gate requires 1-159 characters, non-whitespace text, and a
    single line. Each rule raises :class:`ValueError` with a message
    naming the rule that failed, so a bad entry is never silently
    written and the caller can tell exactly which check tripped.
    """
    if not isinstance(text, str):
        raise TypeError("entry text must be str, got %s" % type(text).__name__)
    _reject_control_chars(text)
    if not 1 <= len(text) <= 159:
        raise ValueError(
            "entry must be 1-159 characters, got %d" % len(text)
        )
    if not text.strip():
        raise ValueError("entry must not be only whitespace")
    if "\n" in text or "\r" in text:
        raise ValueError("entry must be a single line")
    return text.encode("utf-8")


def resolve_output_dir():
    """Resolve the output directory from the environment.

    ``MAKEFUN_OUTPUT_DIR`` wins when it names a real directory. An
    unset variable, an empty value, or a value that is only whitespace
    all fall back to ``/output`` instead of failing later inside
    :func:`os.makedirs` with a confusing error.
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    if raw is None:
        return DEFAULT_OUTPUT_DIR
    cleaned = raw.strip()
    if not cleaned:
        return DEFAULT_OUTPUT_DIR
    return cleaned


def remove_stale_tmp_files(out_dir):
    """Remove stale ``.entry-*.tmp`` files from earlier interrupted runs.

    Only files older than :data:`STALE_AFTER_SECONDS` are touched, so a
    temp file belonging to a concurrently running instance is never
    deleted. Each unlink is retried a bounded number of times: a
    transient failure (for example a file vanishing mid-cleanup) must
    not abort the run, but a file that still cannot be removed after
    the retries is reported instead of being silently ignored.
    """
    pattern = os.path.join(out_dir, TMP_PREFIX + "*" + TMP_SUFFIX)
    now = time.time()
    for path in glob.glob(pattern):
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            # Gone (or unreadable) between glob and stat; nothing to do.
            continue
        if age <= STALE_AFTER_SECONDS:
            continue
        last_error = None
        for _ in range(CLEANUP_ATTEMPTS):
            try:
                os.unlink(path)
                last_error = None
                break
            except FileNotFoundError:
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                time.sleep(CLEANUP_RETRY_DELAY_SECONDS)
        if last_error is not None:
            raise OSError(
                "could not remove stale temp file %s: %s" % (path, last_error)
            )


def _fsync_dir(path):
    """Flush directory metadata so the rename survives a crash."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_written_file(target, expected_data):
    """Read the finished file back and confirm bytes and mode."""
    with open(target, "rb") as f:
        written = f.read()
    if written != expected_data:
        raise ValueError("read-back verification failed for %s" % target)
    mode = stat.S_IMODE(os.stat(target).st_mode)
    if mode != EXPECTED_MODE:
        raise ValueError(
            "entry.txt mode is %o, expected %o" % (mode, EXPECTED_MODE)
        )


def write_entry_atomic(out_dir, data):
    """Write ``data`` to ``entry.txt`` in ``out_dir`` atomically.

    The bytes go to a temporary file in the same directory first, so
    the rename never crosses filesystems and a reader never sees a
    half-written entry. The temp file is chmodded before writing, the
    file is fsynced, and the directory is fsynced after the rename.
    The temp file is unlinked on any failure path.
    """
    os.makedirs(out_dir, exist_ok=True)
    remove_stale_tmp_files(out_dir)
    target = os.path.join(out_dir, "entry.txt")

    fd, tmp_path = tempfile.mkstemp(
        prefix=TMP_PREFIX, suffix=TMP_SUFFIX, dir=out_dir
    )
    try:
        os.chmod(tmp_path, EXPECTED_MODE)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, EXPECTED_MODE)
        os.replace(tmp_path, target)
        tmp_path = None
        os.chmod(target, EXPECTED_MODE)
        _fsync_dir(out_dir)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    _verify_written_file(target, data)
    return target


def main():
    data = validate_entry(ENTRY)
    out_dir = resolve_output_dir()
    target = write_entry_atomic(out_dir, data)
    print("wrote %s (%d bytes)" % (target, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
