"""Send diagnostics and bounded delivery reconciliation; never resends."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from .chatgpt_dom import (
    COMPOSER_FALLBACK_SELECTOR,
    COMPOSER_SELECTOR,
    SEND_BUTTON_BROAD_SELECTOR,
    SEND_BUTTON_FALLBACK_SELECTOR,
    SEND_BUTTON_SELECTOR,
)
from .send_text import submitted_text_matches
from .turn_anchor import normalize_text
from .projection_diagnostics import projection_phase

logger = logging.getLogger(__name__)


async def observe_send(driver, phase, text, anchor=None, *, click_result=None):
    if os.getenv("W2A_SEND_TRACE") != "1":
        return
    record = {"phase": phase, "click_result": click_result}
    try:
        raw = await driver._js_with_data_strict(r"""
        (function() {
          const composer = document.querySelector(__D.composer) || document.querySelector(__D.fallback);
          const canon = s => s.normalize('NFC').replace(/\r\n?/g,'\n').trim();
          const value = composer ? (composer.innerText || composer.value || '') : '';
          const users = [...document.querySelectorAll('[data-message-author-role="user"]')];
          const latest = users[users.length-1];
          const selectors = __D.buttons;
          const candidates = selectors.map(selector => {
            const b = document.querySelector(selector);
            return {selector, present: !!b, enabled: b ? !b.disabled && b.getAttribute('aria-disabled') !== 'true' : false,
                    label: b ? b.getAttribute('aria-label') : null};
          });
          return JSON.stringify({url: location.origin + location.pathname,
            user_count: users.length, latest_user_identity: latest ? latest.getAttribute('data-message-id') : null,
            composer_present: !!composer, composer_length: value.length,
            composer_empty: !!composer && !value.trim(), composer_matches: !!composer && canon(value) === canon(__D.text),
            send_candidates: candidates});
        })()
        """, {"text": text, "composer": COMPOSER_SELECTOR, "fallback": COMPOSER_FALLBACK_SELECTOR,
                "buttons": [SEND_BUTTON_SELECTOR, SEND_BUTTON_FALLBACK_SELECTOR, SEND_BUTTON_BROAD_SELECTOR]})
        record.update(json.loads(raw))
        if record.get("composer_present"):
            record["composer_matches"] = await driver._verify_composer_text(COMPOSER_SELECTOR, text)
        conv = await driver._backend_client._get_live_conversation_id_best_effort()
        record["conversation_id"] = conv
        if anchor:
            record.update({"anchor_mode": anchor.mode, "captured_user_message_id": anchor.captured_user_message_id,
                           "pre_send_latest_user_identity": anchor.latest_user_node_id})
        if conv:
            with projection_phase("send_trace"):
                projection = await driver._backend_client._fetch_recent_conversation_projection(conv)
            users = [n for n in projection.get("nodes", {}).values() if n.get("role") == "user"]
            record["backend_user_count"] = len(users)
            record["backend_exact_text_user_ids"] = [n.get("id") for n in users
                if normalize_text(n.get("text", "")) == normalize_text(text)]
            record["backend_serialized_text_user_ids"] = [n.get("id") for n in users
                if submitted_text_matches(n.get("text", ""), text)]
            latest = max(users, key=lambda n: n.get("create_time") or 0, default={})
            record["backend_latest_user_identity"] = latest.get("id")
    except Exception as exc:
        record["observation_error"] = type(exc).__name__
    logger.info("send_trace %s", json.dumps(record))


def select_delivered_user(projection, anchor, pre_send_ids, conversation_id):
    """Require a trusted existing-conversation baseline and full text match."""
    if (anchor.mode != "existing_conversation" or not isinstance(pre_send_ids, set)
            or conversation_id != anchor.conversation_id_at_capture):
        return None
    candidates = []
    for key, node in projection.get("nodes", {}).items():
        identity = node.get("id") or key
        if (node.get("role") == "user" and identity not in pre_send_ids
                and isinstance(identity, str) and identity
                and submitted_text_matches(node.get("text", ""), anchor.sent_text)
                and isinstance(node.get("create_time"), (float, int))
                and anchor.latest_user_create_time is not None
                and node["create_time"] >= anchor.latest_user_create_time):
            candidates.append(identity)
    return candidates[0] if len(candidates) == 1 else None


async def reconcile_send(driver, anchor, scope, *, timeout=15.0):
    """Bounded evidence reconciliation; no clicks, typing, navigation or resend."""
    from .cdp_driver import AuthExpiredError
    deadline = time.monotonic() + timeout
    last_condition = "submitted_identity_not_observed"
    while time.monotonic() < deadline:
        future = scope.future if scope is not None else None
        if future is not None and future.done() and not future.cancelled():
            result = future.result()
            if result.uuid:
                logger.info("send_confirmation_reconciled: source=late_identity uuid=%s", result.uuid)
                return result.uuid
        try:
            async with asyncio.timeout(max(0.001, deadline - time.monotonic())):
                conv = await driver._backend_client._get_live_conversation_id_best_effort()
                # Backend-only recovery cannot borrow a preexisting fresh/degraded
                # conversation or infer identity from timestamps alone.
                if anchor.mode == "existing_conversation" and conv == anchor.conversation_id_at_capture:
                    with projection_phase("send_confirmation_reconciliation"):
                        projection = await driver._backend_client._fetch_recent_conversation_projection(conv)
                    identity = select_delivered_user(projection, anchor,
                        getattr(driver, "_pre_send_user_ids", None), conv)
                    if identity:
                        empty = await driver._js_strict(
                            "(function(){var el=document.querySelector('" + COMPOSER_SELECTOR + "')"
                            "||document.querySelector('" + COMPOSER_FALLBACK_SELECTOR + "');"
                            "return el ? !(el.innerText || el.value || '').trim() : false;})()"
                        )
                        if empty is True or empty == "true":
                            logger.info("send_confirmation_reconciled: source=backend_full_text uuid=%s conv=%s",
                                        identity, conv)
                            return identity
                        last_condition = "composer_not_proven_empty"
                    else:
                        last_condition = "no_unique_new_full_text_user"
                else:
                    last_condition = "conversation_baseline_not_proven"
        except AuthExpiredError:
            raise
        except Exception as exc:
            last_condition = "probe_failed:" + type(exc).__name__
        remaining = deadline - time.monotonic()
        if remaining > 0:
            # Wait on the live capture when possible; timeout leaves it armed.
            if future is not None and not future.done():
                await asyncio.wait({future}, timeout=min(0.5, remaining))
            else:
                await asyncio.sleep(min(0.5, remaining))
    logger.info("send_confirmation_timeout: condition=%s", last_condition)
    return None
