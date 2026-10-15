"""Small helpers for talking to web services (only the standard library, nothing to install)."""

import json
import ssl
import urllib.error
import urllib.request


def request(method, url, body=None, headers=None, timeout=20, insecure=False):
    """Returns (status, bytes). [body]: dict (sent as JSON) or bytes. Never raises for HTTP errors, only for no connection."""
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "DavePi/0.1 (+https://github.com/Jorn-Jansen/Dave-Pi)")  # (some services block Python's own)
    data = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    context = None
    if insecure:  # (the Hue Bridge has its own certificate)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def json_request(method, url, body=None, headers=None, timeout=20, insecure=False):
    """Like request, with the answer read as JSON (None when it isn't)."""
    status, raw = request(method, url, body, headers, timeout, insecure)
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return status, None


def multipart(fields, files):
    """A multipart/form-data body: [fields] {name: value}, [files] {name: (filename, bytes, content_type)}. Returns (body, content_type)."""
    boundary = "DavePi7d3f"
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, data, kind) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {kind}\r\n\r\n'.encode())
        parts.append(data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
