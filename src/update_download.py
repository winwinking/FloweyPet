# -*- coding: utf-8 -*-
"""Streaming downloader for the one-click update zip.

Standard library only (plus version.py, a plain constant): no Qt, no config,
no paths, no app_log. Nothing here logs; callers decide what to record.

download() streams the body to "<dest_path>.part" while hashing it, aborts
the moment more than expected_size bytes arrive, and only after the size and
SHA-256 both match does it os.replace() the .part onto dest_path. Any failure
deletes the .part and raises UpdateDownloadError carrying a reason code.

The URL check is strict by default and applies to the initial URL and to
every redirect hop: https only, host in ALLOWED_HOSTS. Tests that need a
local http server pass their own `url_validator`; there is no switch, env
var or module-level flag that loosens it.

The final (post-redirect) URL carries a signature, so it is never put in an
exception message -- at most a host name appears there.
"""
import hashlib
import http.client
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

import version

ALLOWED_HOSTS = (
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
)
CHUNK_BYTES = 64 * 1024
SPACE_FACTOR = 3  # free space required = expected_size * 3 (zip + unpacked + slack)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")

# Reason codes carried by UpdateDownloadError.code
NETWORK = "network"
BAD_URL = "bad_url"
BAD_HOST = "bad_host"
TOO_LARGE = "too_large"
SIZE_MISMATCH = "size_mismatch"
HASH_MISMATCH = "hash_mismatch"
NO_SPACE = "no_space"
CANCELLED = "cancelled"


class UpdateDownloadError(Exception):
    """Any download failure. `code` is one of the reason codes above;
    str(exc) is "<code>: <detail>" and never contains a full URL."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _host_for_message(host):
    return (host or "")[:100]


def build_url_validator(allowed_hosts=ALLOWED_HOSTS, allowed_schemes=("https",)):
    """Returns a validator(url) that raises UpdateDownloadError(bad_url /
    bad_host) unless the URL's scheme and host are allowed. The default
    arguments are the strict production policy; tests pass their own."""
    hosts = tuple(h.lower() for h in allowed_hosts)
    schemes = tuple(s.lower() for s in allowed_schemes)

    def validate(url):
        if not isinstance(url, str):
            raise UpdateDownloadError(BAD_URL, "url is not a string")
        try:
            parts = urllib.parse.urlsplit(url)
            host = parts.hostname
            port = parts.port
        except ValueError:
            raise UpdateDownloadError(BAD_URL, "unparsable url") from None
        if parts.scheme.lower() not in schemes:
            raise UpdateDownloadError(BAD_URL, f"scheme {parts.scheme.lower()!r} not allowed")
        if parts.username is not None or parts.password is not None:
            raise UpdateDownloadError(BAD_URL, "credentials in url")
        if not host:
            raise UpdateDownloadError(BAD_URL, "no host")
        if host.lower() not in hosts:
            raise UpdateDownloadError(BAD_HOST, f"host {_host_for_message(host.lower())!r} not allowed")
        if port is not None and parts.scheme.lower() == "https" and port != 443:
            raise UpdateDownloadError(BAD_URL, "unexpected port")
        return None

    return validate


strict_url_validator = build_url_validator()


class _ValidatingRedirect(urllib.request.HTTPRedirectHandler):
    """Runs the URL validator on every redirect target before following it."""

    def __init__(self, validator):
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _notify(progress_cb, done, total):
    if progress_cb is None:
        return
    try:
        progress_cb(done, total)
    except Exception:  # noqa: BLE001 - a broken UI callback must not abort the download
        pass


def _remove_quietly(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateDownloadError(CANCELLED, "cancelled")


def download(url, dest_path, expected_sha256, expected_size, progress_cb=None,
             cancel_event=None, timeout=20, url_validator=strict_url_validator):
    """Download `url` to `dest_path`; returns dest_path on success.

    timeout is per socket operation (connect / each read), not a total.
    progress_cb(downloaded_bytes, expected_size) is called after each chunk.
    cancel_event is a threading.Event; set it to abort (code "cancelled").
    """
    if not (isinstance(expected_sha256, str) and _SHA256_RE.fullmatch(expected_sha256.lower())):
        raise ValueError("expected_sha256 must be 64 hex characters")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError("expected_size must be a positive int")
    expected_sha256 = expected_sha256.lower()
    dest_path = os.fspath(dest_path)
    part_path = dest_path + ".part"

    url_validator(url)
    _check_cancel(cancel_event)

    dest_dir = os.path.dirname(os.path.abspath(dest_path))
    try:
        os.makedirs(dest_dir, exist_ok=True)
        free = shutil.disk_usage(dest_dir).free
    except OSError:
        raise UpdateDownloadError(NO_SPACE, "cannot query free disk space") from None
    if free < expected_size * SPACE_FACTOR:
        raise UpdateDownloadError(NO_SPACE, f"need {expected_size * SPACE_FACTOR} bytes free, have {free}")

    request = urllib.request.Request(url, headers={"User-Agent": f"FloweyPet/{version.VERSION}"})
    opener = urllib.request.build_opener(_ValidatingRedirect(url_validator))

    try:
        _stream_to_part(opener, request, part_path, expected_sha256, expected_size,
                        progress_cb, cancel_event, timeout)
        os.replace(part_path, dest_path)
    except BaseException:
        _remove_quietly(part_path)
        raise
    return dest_path


def _stream_to_part(opener, request, part_path, expected_sha256, expected_size,
                    progress_cb, cancel_event, timeout):
    digest = hashlib.sha256()
    total = 0
    try:
        with opener.open(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    declared = int(declared)
                except ValueError:
                    declared = None
            if declared is not None and declared > expected_size:
                raise UpdateDownloadError(TOO_LARGE, f"server declares {declared} bytes, expected {expected_size}")
            if declared is not None and declared < expected_size:
                raise UpdateDownloadError(SIZE_MISMATCH, f"server declares {declared} bytes, expected {expected_size}")
            try:
                f = open(part_path, "wb")
            except OSError:
                raise UpdateDownloadError(NO_SPACE, "cannot create the temporary download file") from None
            with f:
                while True:
                    _check_cancel(cancel_event)
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected_size:
                        raise UpdateDownloadError(TOO_LARGE, f"more than {expected_size} bytes received")
                    digest.update(chunk)
                    try:
                        f.write(chunk)
                    except OSError:
                        raise UpdateDownloadError(NO_SPACE, "write failed (disk full?)") from None
                    _notify(progress_cb, total, expected_size)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
    except UpdateDownloadError:
        raise
    except urllib.error.HTTPError as e:
        raise UpdateDownloadError(NETWORK, f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise UpdateDownloadError(NETWORK, type(e.reason).__name__) from None
    except http.client.HTTPException as e:
        raise UpdateDownloadError(NETWORK, type(e).__name__) from None
    except OSError as e:  # socket.timeout, connection reset, ...
        raise UpdateDownloadError(NETWORK, type(e).__name__) from None

    if total != expected_size:
        raise UpdateDownloadError(SIZE_MISMATCH, f"received {total} bytes, expected {expected_size}")
    if digest.hexdigest() != expected_sha256:
        raise UpdateDownloadError(HASH_MISMATCH, "sha256 does not match")
