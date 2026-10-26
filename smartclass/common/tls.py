"""Self-signed certificate for the hub, and trust-on-first-use pinning.

There's no certificate authority in a classroom, so the hub makes its own
self-signed certificate once and keeps it. Agents don't check it against a CA;
instead each agent remembers the certificate's fingerprint the first time it
connects (trust-on-first-use) and refuses to connect if it ever changes, which
is what an attacker impersonating the hub would cause. TLS still keeps the
traffic private either way.
"""

import datetime
import hashlib
import hmac
import ssl
from pathlib import Path

from . import config


def ensure_hub_cert(cert_path=None, key_path=None):
    """Return (cert_path, key_path), generating a self-signed pair if needed."""
    folder = config.app_dir()
    cert_path = Path(cert_path) if cert_path else folder / "hub_cert.pem"
    key_path = Path(key_path) if key_path else folder / "hub_key.pem"
    if not (cert_path.exists() and key_path.exists()):
        folder.mkdir(parents=True, exist_ok=True)
        _generate(cert_path, key_path)
    return cert_path, key_path


def _generate(cert_path, key_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SmartClass")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("SmartClass")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass


def server_context(cert_path, key_path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


def client_context():
    """A context that completes the handshake without CA checks.

    Safe only because the agent then verifies the certificate itself by
    comparing its pinned fingerprint (see check_pin).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fingerprint(der_bytes):
    return hashlib.sha256(der_bytes).hexdigest()


def certfile_fingerprint(cert_path):
    pem = Path(cert_path).read_text()
    return fingerprint(ssl.PEM_cert_to_DER_cert(pem))


def peer_fingerprint(ws):
    """SHA-256 of the certificate the hub presented on this connection."""
    ssl_object = ws.transport.get_extra_info("ssl_object")
    if ssl_object is None:
        return None
    return fingerprint(ssl_object.getpeercert(binary_form=True))


def check_pin(pinned, seen):
    """Compare a stored fingerprint with the one just seen.

    "new" (nothing pinned yet), "ok" (matches), or "mismatch" (changed).
    """
    if not pinned:
        return "new"
    return "ok" if hmac.compare_digest(pinned, seen) else "mismatch"
