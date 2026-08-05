"""Standalone cr8 peer process using py-libp2p.

Spike A: tcp + noise transport, stable peer id, allowlist-gated file reads.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import trio

from libp2p import TProtocol, create_new_ed25519_key_pair, new_host
from libp2p.crypto.ed25519 import Ed25519PrivateKey
from libp2p.crypto.keys import KeyPair
from libp2p.crypto.x25519 import (
    X25519PrivateKey,
    create_new_key_pair as create_new_x25519_key_pair,
)
from libp2p.network.stream.net_stream import INetStream
from libp2p.security.noise.transport import (
    PROTOCOL_ID as NOISE_PROTOCOL_ID,
    Transport as NoiseTransport,
)
from multiaddr import Multiaddr

PROTOCOL_ID = TProtocol("/cr8/mirror/1.0.0")
MAX_FRAME_BYTES = 64 * 1024  # 64 KB cap on incoming JSON frames
CHUNK_SIZE = 64 * 1024       # 64 KB per disk/network chunk

log = logging.getLogger("cr8.peer")


# ---------------------------------------------------------------------------
# Framing helpers
# ---------------------------------------------------------------------------

async def _read_exactly(stream: INetStream, n: int) -> bytes:
    """Read exactly *n* bytes from the stream."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = await stream.read(n - len(buf))
        except Exception as exc:
            raise EOFError(f"stream closed: {exc}") from exc
        if not chunk:
            raise EOFError("stream closed unexpectedly")
        buf.extend(chunk)
    return bytes(buf)


async def read_framed_msg(stream: INetStream) -> dict:
    """Read a 4-byte big-endian length-prefixed JSON frame.

    Returns an empty dict on EOF or if the frame exceeds MAX_FRAME_BYTES.
    """
    try:
        raw_len = await _read_exactly(stream, 4)
    except EOFError:
        return {}
    msg_len = int.from_bytes(raw_len, "big")
    if msg_len > MAX_FRAME_BYTES:
        log.warning("incoming frame too large (%d bytes), dropping", msg_len)
        return {}
    try:
        data = await _read_exactly(stream, msg_len)
    except EOFError:
        return {}
    return json.loads(data.decode("utf-8"))


async def write_framed_msg(stream: INetStream, header: dict) -> None:
    """Write a 4-byte big-endian length-prefixed JSON frame."""
    payload = json.dumps(header).encode("utf-8")
    await stream.write(len(payload).to_bytes(4, "big") + payload)


# ---------------------------------------------------------------------------
# Key persistence
# ---------------------------------------------------------------------------

def _load_or_create_key(secrets_dir: Path) -> tuple[KeyPair, KeyPair]:
    """Load (or generate) the Ed25519 identity key and X25519 Noise DH key.

    Both keys are stored in *secrets_dir* with mode 0600.  The identity key
    is used as the libp2p peer ID; the X25519 key is the Noise static DH key
    (Noise requires X25519, not Ed25519, for the DH step).
    """
    secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        secrets_dir.chmod(0o700)
    except OSError:
        pass

    def _load_or_write(fname: str, generate, from_bytes_fn) -> KeyPair:
        key_file = secrets_dir / fname
        if key_file.is_file():
            try:
                priv = from_bytes_fn(key_file.read_bytes())
                return KeyPair(priv, priv.get_public_key())
            except Exception as exc:
                log.warning("failed to load %s (%s); regenerating", fname, exc)
        kp = generate()
        fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(kp.private_key.to_bytes())
        return kp

    identity_kp = _load_or_write(
        "peer.key",
        create_new_ed25519_key_pair,
        lambda b: Ed25519PrivateKey.from_bytes(b),
    )
    noise_kp = _load_or_write(
        "peer_noise.key",
        create_new_x25519_key_pair,
        lambda b: X25519PrivateKey.from_bytes(b),
    )
    return identity_kp, noise_kp


# ---------------------------------------------------------------------------
# Peer
# ---------------------------------------------------------------------------

class Cr8Peer:
    """Standalone P2P transport peer.

    Parameters
    ----------
    host_ip:
        Interface to bind.  Defaults to ``127.0.0.1``; callers that pass
        anything else **must** also supply a non-empty *allowed_peers* set.
    port:
        TCP port (0 = OS-assigned).
    mirror_root:
        Directory that will be served.  Must not be deleted during operation.
    secrets_dir:
        Directory for the persistent identity key (mode 0700).  Survives
        mirror rebuilds because it is separate from *mirror_root*.
    allowed_peers:
        Set of peer-ID strings that may read files.  ``None`` means allow
        all (safe only on loopback).
    """

    def __init__(
        self,
        host_ip: str = "127.0.0.1",
        port: int = 0,
        mirror_root: Path = Path("mirror"),
        secrets_dir: Path = Path("secrets"),
        allowed_peers: set[str] | None = None,
    ) -> None:
        if host_ip != "127.0.0.1" and not allowed_peers:
            raise ValueError(
                "binding to a non-loopback address requires an explicit "
                "--allowlist; refusing to start an open file server"
            )
        self.host_ip = host_ip
        self.port = port
        self.mirror_root = Path(mirror_root).resolve()
        self.allowed_peers = allowed_peers  # None = unrestricted (loopback only)
        self._identity_kp, self._noise_kp = _load_or_create_key(Path(secrets_dir))
        self._key_pair = self._identity_kp  # expose for tests
        self.host = None
        self._running = False
        # peer_id -> set of active CancelScopes (one per in-flight stream)
        self._peer_scopes: dict[str, set[trio.CancelScope]] = defaultdict(set)

    @asynccontextmanager
    async def run(self):
        """Async context manager that starts the peer and tears it down cleanly."""
        listen_addrs = [Multiaddr(f"/ip4/{self.host_ip}/tcp/{self.port}")]
        noise = NoiseTransport(self._identity_kp, self._noise_kp.private_key)
        self.host = new_host(
            key_pair=self._identity_kp,
            sec_opt={NOISE_PROTOCOL_ID: noise},
            listen_addrs=listen_addrs,
        )
        self.host.set_stream_handler(PROTOCOL_ID, self.handle_stream)
        async with self.host.run(listen_addrs):
            self._running = True
            log.info("peer started, listening on %s", self.host.get_addrs())
            try:
                yield self
            finally:
                self._running = False
                log.info("peer stopped")

    # ------------------------------------------------------------------
    # Stream dispatch
    # ------------------------------------------------------------------

    async def handle_stream(self, stream: INetStream) -> None:
        peer_id = str(stream.muxed_conn.peer_id)

        with trio.CancelScope() as scope:
            self._peer_scopes[peer_id].add(scope)
            try:
                # ---- allowlist check before any I/O ----
                if self.allowed_peers is not None and peer_id not in self.allowed_peers:
                    log.warning("unauthorized peer %s; closing", peer_id)
                    await write_framed_msg(
                        stream, {"status": "error", "message": "Unauthorized peer"}
                    )
                    return

                req = await read_framed_msg(stream)
                if not req:
                    return

                action = req.get("action")
                if action == "read_range":
                    await self._handle_read_range(stream, req)
                elif action == "cancel":
                    # Client cancels its own in-flight transfers.
                    log.info("peer %s sent cancel", peer_id)
                    self._cancel_peer_scopes(peer_id, except_scope=scope)
                elif action == "revoke":
                    # Administrative: remove a target peer from the allowlist
                    # and drop all its active streams.
                    target = req.get("target_peer_id", "")
                    if not target:
                        await write_framed_msg(
                            stream,
                            {"status": "error", "message": "target_peer_id required"},
                        )
                        return
                    log.info("peer %s revoked %s", peer_id, target)
                    if self.allowed_peers is not None:
                        self.allowed_peers.discard(target)
                    self._cancel_peer_scopes(target, except_scope=None)
                    await write_framed_msg(stream, {"status": "ok"})
                else:
                    await write_framed_msg(
                        stream, {"status": "error", "message": f"unknown action: {action}"}
                    )
            except trio.Cancelled:
                log.info("stream cancelled for peer %s", peer_id)
                raise
            except Exception as exc:
                log.error("error handling stream for peer %s: %s", peer_id, exc)
            finally:
                self._peer_scopes[peer_id].discard(scope)
                if not self._peer_scopes[peer_id]:
                    self._peer_scopes.pop(peer_id, None)
                # Shield so the close is not itself cancelled.
                with trio.CancelScope(shield=True):
                    await stream.close()

    def _cancel_peer_scopes(
        self,
        peer_id: str,
        *,
        except_scope: trio.CancelScope | None,
    ) -> None:
        """Cancel all active CancelScopes for *peer_id*, optionally skipping one."""
        for s in list(self._peer_scopes.get(peer_id, ())):
            if s is not except_scope:
                s.cancel()

    # ------------------------------------------------------------------
    # read_range handler
    # ------------------------------------------------------------------

    async def _handle_read_range(self, stream: INetStream, req: dict) -> None:
        # ---- validate path ----
        filename = req.get("filename", "")
        filepath = (self.mirror_root / filename).resolve()
        if not filepath.is_relative_to(self.mirror_root):
            await write_framed_msg(
                stream, {"status": "error", "message": "Access denied"}
            )
            return
        if not filepath.is_file():
            await write_framed_msg(
                stream, {"status": "error", "message": "File not found"}
            )
            return

        # ---- validate start / end before sending OK ----
        raw_start = req.get("start", 0)
        raw_end = req.get("end", None)
        try:
            start = int(raw_start)
            end = int(raw_end) if raw_end is not None else None
        except (TypeError, ValueError):
            await write_framed_msg(
                stream, {"status": "error", "message": "start and end must be integers"}
            )
            return
        if start < 0 or (end is not None and end < 0):
            await write_framed_msg(
                stream, {"status": "error", "message": "start and end must be non-negative"}
            )
            return
        if end is not None and end <= start:
            await write_framed_msg(
                stream, {"status": "error", "message": "end must be greater than start"}
            )
            return

        file_size = filepath.stat().st_size
        content_length = (end - start) if end is not None else (file_size - start)

        # ---- send OK header with content_length so client detects truncation ----
        await write_framed_msg(
            stream, {"status": "ok", "content_length": content_length}
        )

        # ---- stream file in bounded chunks ----
        bytes_remaining = content_length
        with open(filepath, "rb") as fh:
            fh.seek(start)
            while bytes_remaining > 0:
                await trio.lowlevel.checkpoint()
                chunk = fh.read(min(bytes_remaining, CHUNK_SIZE))
                if not chunk:
                    break
                try:
                    await stream.write(chunk)
                except Exception as exc:
                    log.info("write failed for %s (%s), aborting", peer_id, exc)
                    break
                bytes_remaining -= len(chunk)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main(
    host_ip: str,
    port: int,
    mirror_root: Path,
    secrets_dir: Path,
    allowed_peers: set[str] | None,
) -> None:
    peer = Cr8Peer(
        host_ip=host_ip,
        port=port,
        mirror_root=mirror_root,
        secrets_dir=secrets_dir,
        allowed_peers=allowed_peers,
    )
    async with peer.run():
        await trio.sleep_forever()


def run_peer(
    host_ip: str,
    port: int,
    mirror_root: Path,
    secrets_dir: Path,
    allowed_peers: set[str] | None,
) -> None:
    trio.run(_main, host_ip, port, mirror_root, secrets_dir, allowed_peers)
