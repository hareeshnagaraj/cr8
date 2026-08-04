"""The collection share dialog keeps the v1 product contract visible in source."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "app" / "collections" / "page.tsx"
DIALOG = ROOT / "web" / "components" / "CollectionShareDialog.tsx"
DYNAMIC_PAGE = ROOT / "web" / "app" / "collections" / "[ulid]" / "page.tsx"
CSS = ROOT / "web" / "app" / "globals.css"


def test_collection_page_owns_the_make_a_link_dialog() -> None:
    page = PAGE.read_text(encoding="utf-8")
    assert "CollectionShareDialog" in page
    assert ">\n              Make a link\n            </button>" in page
    assert "collectionUlid={openUlid}" in page
    assert "collectionName={openCollection.name}" in page


def test_collection_share_dialog_carries_the_full_mutation_contract() -> None:
    dialog = DIALOG.read_text(encoding="utf-8")
    assert 'collection_ulid: collectionUlid' in dialog
    assert "ttl_hours: ttlHours" in dialog
    assert "maxLength={280}" in dialog
    assert '"X-CR8-Request": "1"' in dialog
    assert "7 days" in dialog
    assert "24 hours" in dialog
    assert "Copied" in dialog
    assert "Revoke" in dialog
    assert "Re-mint" in dialog
    assert "This link plays the album as it was when you made it." in dialog
    remint = dialog.split("async function remintLink()", 1)[1]
    assert remint.index("/revoke`") < remint.index("const created = await requestMint()")
    assert "<select" not in dialog


def test_member_redirect_target_is_a_real_next_collection_page() -> None:
    dynamic = DYNAMIC_PAGE.read_text(encoding="utf-8")
    assert "CollectionsView" in dynamic
    assert "initialUlid={ulid}" in dynamic


def test_collection_dialog_uses_the_existing_layer_scale_and_mobile_targets() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert ".scrim {\n  position: fixed; inset: 0; z-index: 75;" in css
    assert ".collection-share-dialog { width: min(520px, 100%); }" in css
    assert ".collection-share-choices button {" in css
    assert "flex: 1; min-height: 44px" in css
    assert ".dialog .menu-panel.is-flipped" in css
    assert "top: calc(100% + 6px); bottom: auto" in css
