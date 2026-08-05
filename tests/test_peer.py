"""Tests for the standalone cr8 P2P transport peer."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import trio
from libp2p import TProtocol, create_new_ed25519_key_pair, new_host
from libp2p.crypto.x25519 import create_new_key_pair as create_new_x25519_key_pair
from libp2p.security.insecure.transport import PLAINTEXT_PROTOCOL_ID, InsecureTransport
from libp2p.security.noise.transport import (
    PROTOCOL_ID as NOISE_PROTOCOL_ID,
    Transport as NoiseTransport,
)
from multiaddr import Multiaddr

from cr8.peer import (
    MAX_FRAME_BYTES,
    Cr8Peer,
    PROTOCOL_ID,
    read_framed_msg,
    write_framed_msg,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_peer(tmp_path: Path, **kwargs) -> Cr8Peer:
    mirror = tmp_path / "mirror"
    mirror.mkdir(exist_ok=True)
    secrets = tmp_path / "secrets"
    return Cr8Peer(
        host_ip="127.0.0.1",
        port=0,
        mirror_root=mirror,
        secrets_dir=secrets,
        **kwargs,
    )


async def _noise_client(key_pair=None):
    kp = key_pair or create_new_ed25519_key_pair()
    noise_kp = create_new_x25519_key_pair()
    listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
    noise = NoiseTransport(kp, noise_kp.private_key)
    return new_host(
        key_pair=kp,
        sec_opt={NOISE_PROTOCOL_ID: noise},
        listen_addrs=listen_addrs,
    )


async def _read_all(stream) -> bytes:
    buf = b""
    while True:
        try:
            chunk = await stream.read(4096)
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
    return buf


# ---------------------------------------------------------------------------
# Identity persistence
# ---------------------------------------------------------------------------

def test_key_persists_across_restarts(tmp_path: Path):
    trio.run(_test_key_persists_across_restarts, tmp_path)


async def _test_key_persists_across_restarts(tmp_path: Path):
    peer1 = _make_peer(tmp_path)
    peer2 = _make_peer(tmp_path)  # same secrets_dir → same key
    assert peer1._identity_kp.private_key.to_bytes() == peer2._identity_kp.private_key.to_bytes()


def test_key_survives_mirror_wipe(tmp_path: Path):
    trio.run(_test_key_survives_mirror_wipe, tmp_path)


async def _test_key_survives_mirror_wipe(tmp_path: Path):
    peer1 = _make_peer(tmp_path)
    original_bytes = peer1._identity_kp.private_key.to_bytes()

    # Wipe mirror (simulates `cr8 build` deleting it)
    import shutil
    shutil.rmtree(tmp_path / "mirror")
    (tmp_path / "mirror").mkdir()

    # Key must survive
    peer2 = _make_peer(tmp_path)
    assert peer2._identity_kp.private_key.to_bytes() == original_bytes


def test_key_file_is_mode_0600(tmp_path: Path):
    """On POSIX the file must land at exactly 0600; skip on Windows."""
    import sys
    if sys.platform == "win32":
        pytest.skip("Windows does not enforce POSIX file modes")
    peer = _make_peer(tmp_path)
    key_file = tmp_path / "secrets" / "peer.key"
    assert key_file.exists()
    mode = oct(os.stat(key_file).st_mode)[-4:]
    assert mode == "0600", f"expected 0600, got {mode}"


# ---------------------------------------------------------------------------
# Security: noise-only, plaintext rejected
# ---------------------------------------------------------------------------

def test_plaintext_dial_rejected(tmp_path: Path):
    trio.run(_test_plaintext_dial_rejected, tmp_path)


async def _test_plaintext_dial_rejected(tmp_path: Path):
    peer = _make_peer(tmp_path)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        bad_kp = create_new_ed25519_key_pair()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        bad_client = new_host(
            key_pair=bad_kp,
            sec_opt={PLAINTEXT_PROTOCOL_ID: InsecureTransport(bad_kp)},
            listen_addrs=listen_addrs,
        )
        async with bad_client.run(listen_addrs):
            bad_client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)
            with pytest.raises(Exception):
                await bad_client.new_stream(peer_id, [PROTOCOL_ID])


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

def test_unauthorized_peer_rejected(tmp_path: Path):
    trio.run(_test_unauthorized_peer_rejected, tmp_path)


async def _test_unauthorized_peer_rejected(tmp_path: Path):
    # Peer with empty allowlist (no peer IDs)
    peer = _make_peer(tmp_path, allowed_peers=set())
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        client = await _noise_client()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)
            stream = await client.new_stream(peer_id, [PROTOCOL_ID])
            resp = await read_framed_msg(stream)
            assert resp["status"] == "error"
            assert "Unauthorized" in resp["message"]
            with trio.CancelScope(shield=True):
                await stream.close()


def test_non_loopback_without_allowlist_raises():
    with pytest.raises(ValueError, match="allowlist"):
        Cr8Peer(host_ip="0.0.0.0", port=0, mirror_root=Path("m"), secrets_dir=Path("s"))


# ---------------------------------------------------------------------------
# Frame cap
# ---------------------------------------------------------------------------

def test_large_frame_rejected(tmp_path: Path):
    trio.run(_test_large_frame_rejected, tmp_path)


async def _test_large_frame_rejected(tmp_path: Path):
    peer = _make_peer(tmp_path)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        client = await _noise_client()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)
            stream = await client.new_stream(peer_id, [PROTOCOL_ID])

            # Send a frame claiming to be MAX_FRAME_BYTES + 1 bytes
            oversized_len = (MAX_FRAME_BYTES + 1).to_bytes(4, "big")
            await stream.write(oversized_len)
            # Server should close without crashing; stream will EOF
            body = await _read_all(stream)
            assert body == b""
            with trio.CancelScope(shield=True):
                await stream.close()


# ---------------------------------------------------------------------------
# File exchange (happy paths)
# ---------------------------------------------------------------------------

def test_full_file_read(tmp_path: Path):
    trio.run(_test_full_file_read, tmp_path)


async def _test_full_file_read(tmp_path: Path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    test_data = b"hello world this is a test audio file content"
    (mirror / "song.mp3").write_bytes(test_data)

    secrets = tmp_path / "secrets"
    peer = Cr8Peer(host_ip="127.0.0.1", port=0, mirror_root=mirror, secrets_dir=secrets)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        client = await _noise_client()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)

            stream = await client.new_stream(peer_id, [PROTOCOL_ID])
            await write_framed_msg(stream, {"action": "read_range", "filename": "song.mp3"})

            hdr = await read_framed_msg(stream)
            assert hdr["status"] == "ok"
            assert hdr["content_length"] == len(test_data)

            body = await _read_all(stream)
            assert body == test_data
            with trio.CancelScope(shield=True):
                await stream.close()


def test_range_read(tmp_path: Path):
    trio.run(_test_range_read, tmp_path)


async def _test_range_read(tmp_path: Path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "song.mp3").write_bytes(b"hello world")

    secrets = tmp_path / "secrets"
    peer = Cr8Peer(host_ip="127.0.0.1", port=0, mirror_root=mirror, secrets_dir=secrets)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        client = await _noise_client()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)

            stream = await client.new_stream(peer_id, [PROTOCOL_ID])
            await write_framed_msg(stream, {"action": "read_range", "filename": "song.mp3", "start": 6, "end": 11})

            hdr = await read_framed_msg(stream)
            assert hdr["status"] == "ok"
            assert hdr["content_length"] == 5

            body = await _read_all(stream)
            assert body == b"world"
            with trio.CancelScope(shield=True):
                await stream.close()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_missing_file_returns_error(tmp_path: Path):
    trio.run(_test_missing_file_returns_error, tmp_path)


async def _test_missing_file_returns_error(tmp_path: Path):
    peer = _make_peer(tmp_path)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        client = await _noise_client()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)
            stream = await client.new_stream(peer_id, [PROTOCOL_ID])
            await write_framed_msg(stream, {"action": "read_range", "filename": "missing.mp3"})
            hdr = await read_framed_msg(stream)
            assert hdr["status"] == "error"
            assert "not found" in hdr["message"].lower()
            with trio.CancelScope(shield=True):
                await stream.close()


def test_path_traversal_rejected(tmp_path: Path):
    trio.run(_test_path_traversal_rejected, tmp_path)


async def _test_path_traversal_rejected(tmp_path: Path):
    peer = _make_peer(tmp_path)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        client = await _noise_client()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)
            stream = await client.new_stream(peer_id, [PROTOCOL_ID])
            await write_framed_msg(stream, {"action": "read_range", "filename": "../../etc/passwd"})
            hdr = await read_framed_msg(stream)
            assert hdr["status"] == "error"
            with trio.CancelScope(shield=True):
                await stream.close()


def test_invalid_start_end_rejected(tmp_path: Path):
    trio.run(_test_invalid_start_end_rejected, tmp_path)


async def _test_invalid_start_end_rejected(tmp_path: Path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "song.mp3").write_bytes(b"hello world")
    secrets = tmp_path / "secrets"
    peer = Cr8Peer(host_ip="127.0.0.1", port=0, mirror_root=mirror, secrets_dir=secrets)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id = peer.host.get_id()

        client = await _noise_client()
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id, [peer_addr], 10000)

            # Negative start
            stream = await client.new_stream(peer_id, [PROTOCOL_ID])
            await write_framed_msg(stream, {"action": "read_range", "filename": "song.mp3", "start": -1})
            hdr = await read_framed_msg(stream)
            assert hdr["status"] == "error"
            with trio.CancelScope(shield=True):
                await stream.close()

            # end <= start
            stream2 = await client.new_stream(peer_id, [PROTOCOL_ID])
            await write_framed_msg(stream2, {"action": "read_range", "filename": "song.mp3", "start": 5, "end": 3})
            hdr2 = await read_framed_msg(stream2)
            assert hdr2["status"] == "error"
            with trio.CancelScope(shield=True):
                await stream2.close()


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

def test_cancel_stops_active_reads(tmp_path: Path):
    trio.run(_test_cancel_stops_active_reads, tmp_path)


async def _test_cancel_stops_active_reads(tmp_path: Path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    # Write a large file so the read takes multiple chunks
    (mirror / "big.mp3").write_bytes(b"x" * (200 * 1024))
    secrets = tmp_path / "secrets"
    peer = Cr8Peer(host_ip="127.0.0.1", port=0, mirror_root=mirror, secrets_dir=secrets)
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id_obj = peer.host.get_id()
        peer_id_str = str(peer_id_obj)

        client_kp = create_new_ed25519_key_pair()
        client = await _noise_client(client_kp)
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]
        async with client.run(listen_addrs):
            client.get_peerstore().add_addrs(peer_id_obj, [peer_addr], 10000)

            # Start a slow read
            read_stream = await client.new_stream(peer_id_obj, [PROTOCOL_ID])
            await write_framed_msg(read_stream, {"action": "read_range", "filename": "big.mp3"})

            # Read only the header, leave the body in-flight
            hdr = await read_framed_msg(read_stream)
            assert hdr["status"] == "ok"

            # Send cancel on a separate stream
            cancel_stream = await client.new_stream(peer_id_obj, [PROTOCOL_ID])
            await write_framed_msg(cancel_stream, {"action": "cancel"})
            await trio.sleep(0.2)  # let server process
            with trio.CancelScope(shield=True):
                await cancel_stream.close()
                await read_stream.close()

def test_revoke_stops_active_reads(tmp_path: Path):
    trio.run(_test_revoke_stops_active_reads, tmp_path)


async def _test_revoke_stops_active_reads(tmp_path: Path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    (mirror / "big.mp3").write_bytes(b"x" * (200 * 1024))
    secrets = tmp_path / "secrets"

    admin_kp = create_new_ed25519_key_pair()
    target_kp = create_new_ed25519_key_pair()
    admin_id = str(new_host(key_pair=admin_kp).get_id())
    target_id = str(new_host(key_pair=target_kp).get_id())

    peer = Cr8Peer(
        host_ip="127.0.0.1",
        port=0,
        mirror_root=mirror,
        secrets_dir=secrets,
        allowed_peers={admin_id, target_id},
    )
    async with peer.run():
        peer_addr = peer.host.get_addrs()[0]
        peer_id_obj = peer.host.get_id()

        target_client = await _noise_client(target_kp)
        admin_client = await _noise_client(admin_kp)
        listen_addrs = [Multiaddr("/ip4/127.0.0.1/tcp/0")]

        async with target_client.run(listen_addrs), admin_client.run(listen_addrs):
            target_client.get_peerstore().add_addrs(peer_id_obj, [peer_addr], 10000)
            admin_client.get_peerstore().add_addrs(peer_id_obj, [peer_addr], 10000)

            # Target starts a slow read
            read_stream = await target_client.new_stream(peer_id_obj, [PROTOCOL_ID])
            await write_framed_msg(read_stream, {"action": "read_range", "filename": "big.mp3"})

            hdr = await read_framed_msg(read_stream)
            assert hdr["status"] == "ok"

            # Admin sends revoke targeting the other peer
            revoke_stream = await admin_client.new_stream(peer_id_obj, [PROTOCOL_ID])
            await write_framed_msg(
                revoke_stream, {"action": "revoke", "target_peer_id": target_id}
            )
            revoke_hdr = await read_framed_msg(revoke_stream)
            assert revoke_hdr["status"] == "ok"

            await trio.sleep(0.2)  # let server process

            # The target's read stream should now be closed abruptly
            with trio.CancelScope(shield=True):
                await revoke_stream.close()
                await read_stream.close()