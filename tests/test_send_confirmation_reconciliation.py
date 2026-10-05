import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatgpt_web2api.cdp_driver import AuthExpiredError
from chatgpt_web2api.identity_listener import CaptureResult, IdentityListener, hash_sent_text
from chatgpt_web2api.send_confirmation import reconcile_send, select_delivered_user
from chatgpt_web2api.send_text import submitted_text_matches
from chatgpt_web2api.turn_anchor import TurnAnchor


@pytest.mark.parametrize("expected,actual,match", [
    ("a *diff*", r"a \*diff\*", True),
    ("x <id> http://host", r"x \<id> http\://host", True),
    ("https://host/path", "[https://host/path](https://host/path)", True),
    ("https://host/path)", r"[https://host/path)](https://host/path\))", True),
    (r"literal \*", r"literal \\\*", True),
    (r"literal \*", "literal *", False),
    ("https://host/path", "[https://host/path](https://evil/path)", False),
    ("https://host/path", "[label](https://host/path)", False),
    ("a *diff*", r"b \*diff\*", False),
    ("a *diff*", r"a \*diff\* suffix", False),
    ("a *diff* tail", r"a \*diff\*", False),
    ("space  matters", "space matters", False),
    ("line\n", "line", False),
    ("", "", False),
])
def test_full_text_serialization(expected, actual, match):
    assert submitted_text_matches(actual, expected) is match


def anchor():
    return TurnAnchor(sent_text="a *diff*", mode="existing_conversation",
                      conversation_id_at_capture="conv", latest_user_node_id="old",
                      latest_user_create_time=10)


def projection(identity="new", text=r"a \*diff\*", create_time=11):
    return {"nodes": {identity: {"id": identity, "role": "user", "text": text,
                                "create_time": create_time}}}


@pytest.mark.parametrize("mapping,ids,conv,expected", [
    (projection(), {"old"}, "conv", "new"),
    (projection("old"), {"old"}, "conv", None),
    (projection(text="unrelated"), {"old"}, "conv", None),
    (projection(create_time=9), {"old"}, "conv", None),
    (projection(), None, "conv", None),
    (projection(), {"old"}, "other", None),
    ({"nodes": {**projection("new")["nodes"], **projection("second")["nodes"]}}, {"old"}, "conv", None),
])
def test_backend_selection(mapping, ids, conv, expected):
    assert select_delivered_user(mapping, anchor(), ids, conv) == expected


@pytest.mark.asyncio
async def test_listener_serialized_post_and_late_capture():
    listener = IdentityListener(MagicMock())
    scope = listener.arm_capture_scope(expected_text_hash=hash_sent_text("a *diff*"),
        expected_text="a *diff*", conversation_id="conv", target_id="tab")
    assert await listener.wait_for_captured_uuid(timeout=0.001) is None
    assert not scope.future.cancelled()
    uid="12345678-1234-1234-1234-123456789abc"
    post = {"action": "next", "conversation_id": "conv", "messages": [
        {"id": uid, "author": {"role": "user"}, "content": {"parts": [r"a \*diff\*"]}}]}
    await listener._process_send_post(scope, {"params": {"request": {"postData": json.dumps(post)}}}, "")
    assert await listener.wait_for_captured_uuid(timeout=0.001) == uid
    scope.close()


def driver(mapping=None):
    d = MagicMock()
    d._pre_send_user_ids = {"old"}
    d._backend_client._get_live_conversation_id_best_effort = AsyncMock(return_value="conv")
    d._backend_client._fetch_recent_conversation_projection = AsyncMock(return_value=mapping or projection())
    d._js_strict = AsyncMock(return_value=True)
    return d


@pytest.mark.asyncio
async def test_backend_recovery_waits_for_propagation_and_never_sends():
    d = driver()
    d._backend_client._fetch_recent_conversation_projection.side_effect = [projection("old"), projection()]
    assert await reconcile_send(d, anchor(), None, timeout=0.7) == "new"
    assert not d.click_send.called and not d.type_message.called


@pytest.mark.asyncio
async def test_cleared_composer_is_required_for_backend_only_recovery():
    d=driver()
    d._js_strict.return_value=False
    assert await reconcile_send(d, anchor(), None, timeout=0.001) is None
    assert not d.click_send.called


@pytest.mark.asyncio
async def test_transport_failure_never_confirms_or_resends():
    d=driver()
    d._backend_client._fetch_recent_conversation_projection.side_effect=RuntimeError("offline")
    assert await reconcile_send(d, anchor(), None, timeout=0.001) is None
    assert not d.click_send.called


@pytest.mark.asyncio
async def test_auth_failure_propagates():
    d=driver()
    d._backend_client._fetch_recent_conversation_projection.side_effect=AuthExpiredError("login")
    with pytest.raises(AuthExpiredError):
        await reconcile_send(d, anchor(), None, timeout=0.1)


@pytest.mark.asyncio
async def test_late_scoped_identity_does_not_require_dom_counts():
    d=driver()
    scope=MagicMock()
    scope.future=asyncio.get_running_loop().create_future()
    scope.future.set_result(CaptureResult(uuid="late-validated-id"))
    assert await reconcile_send(d, anchor(), scope, timeout=0.1) == "late-validated-id"
    d._backend_client._fetch_recent_conversation_projection.assert_not_called()


@pytest.mark.parametrize("mode", ["fresh_chat", "degraded_existing"])
def test_untrusted_baseline_cannot_recover_backend_identity(mode):
    a=TurnAnchor(sent_text="a *diff*", mode=mode, conversation_id_at_capture="conv")
    assert select_delivered_user(projection(), a, {"old"}, "conv") is None
