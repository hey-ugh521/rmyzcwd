"""Phase 4F-2B-2 Shadow evaluator tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from core import capabilities, identity, identity_shadow as shadow, store  # noqa: E402


class ShadowEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        self.old_core = store.CFG.get("core")
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "evaluate.db"
        store.CFG["core"] = {**(self.old_core or {}), "api_key": "shadow-legacy-key"}
        store.init_db()
        identity.init_schema()
        capabilities.init_schema()
        shadow.init_schema()
        identity.resolve_legacy_api_key("shadow-legacy-key")
        self.legacy = shadow.LegacyAuthObservation(True, "api-key", "accepted")

    def tearDown(self) -> None:
        store.close_anchor()
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        if self.old_core is None:
            store.CFG.pop("core", None)
        else:
            store.CFG["core"] = self.old_core
        self.tmp.cleanup()

    def test_route_resolver_coverage(self):
        self.assertEqual(shadow.resolve_route("GET", "/api/registry/character/x").route_id,
                         "registry.read")
        self.assertEqual(shadow.resolve_route("POST", "/api/registry/character").action, "write")
        self.assertEqual(shadow.resolve_route("POST", "/api/capabilities/permissions").route_id,
                         "policy.permissions.write")
        self.assertFalse(shadow.resolve_route("GET", "/api/capabilities/invocations").covered)
        self.assertFalse(shadow.resolve_route("GET", "/v1/chat").covered)
        self.assertEqual(shadow.resolve_route("GET", "/api/registry/character").resource_type,
                         "character")

    def test_action_follows_http_method_not_path_prefix(self):
        maintenance_get = shadow.resolve_route("GET", "/api/capabilities/maintenance")
        maintenance_post = shadow.resolve_route("POST", "/api/capabilities/maintenance")
        health_post = shadow.resolve_route("POST", "/api/capabilities/provider-health")
        self.assertEqual(maintenance_get.action, "read")
        self.assertEqual(maintenance_post.action, "write")
        self.assertEqual(health_post.action, "write")
        self.assertEqual(shadow.route_decision("operator", maintenance_get.action), "allow")
        self.assertEqual(shadow.route_decision("operator", health_post.action), "deny")

    def test_capability_prefixes_are_segment_bounded(self):
        for path in (
            "/api/capabilities/permissions-evil",
            "/api/capabilities/permissionsXYZ",
            "/api/capabilities/provider-healthz",
            "/api/capabilities/maintenancefoo",
        ):
            self.assertFalse(shadow.resolve_route("GET", path).covered, path)
        self.assertFalse(shadow.resolve_route(
            "POST", "/api/capabilities/invocations-evil").covered)

    def test_route_policy_role_matrix(self):
        self.assertEqual(shadow.route_decision("admin", "read"), "allow")
        self.assertEqual(shadow.route_decision("admin", "write"), "allow")
        self.assertEqual(shadow.route_decision("operator", "read"), "allow")
        self.assertEqual(shadow.route_decision("operator", "write"), "deny")
        self.assertEqual(shadow.route_decision("service", "read"), "deny")
        self.assertEqual(shadow.route_decision("service", "write"), "deny")

    def test_legacy_resolved_service_route_denies_management(self):
        event = shadow.evaluate_shadow(
            method="GET", path="/api/registry/character/x", bearer_token="shadow-legacy-key",
            legacy_auth=self.legacy, request_id="req-1",
        )
        self.assertEqual(event["evaluation_state"], "evaluated")
        self.assertEqual(event["policy_domain"], "route")
        self.assertEqual(event["identity_state"], "resolved")
        self.assertEqual(event["role"], "service")
        self.assertEqual(event["policy_result"], "deny")

    def test_unknown_bearer_is_unmapped_without_identity_write(self):
        before = len(identity.list_principals())
        event = shadow.evaluate_shadow(
            method="GET", path="/api/registry/character/x", bearer_token="unknown",
            legacy_auth=self.legacy, request_id="req-2",
        )
        self.assertEqual(event["identity_state"], "unmapped")
        self.assertEqual(event["evaluation_state"], "insufficient_context")
        self.assertEqual(len(identity.list_principals()), before)

    def test_legacy_denied_does_not_map_identity(self):
        before = len(identity.list_principals())
        event = shadow.evaluate_shadow(
            method="GET", path="/api/registry/character/x", bearer_token="unknown",
            legacy_auth=shadow.LegacyAuthObservation(False, None, "denied"),
            request_id="req-3",
        )
        self.assertEqual(event["identity_state"], "legacy_denied")
        self.assertEqual(event["evaluation_state"], "insufficient_context")
        self.assertEqual(len(identity.list_principals()), before)

    def test_legacy_anonymous_is_not_allow(self):
        event = shadow.evaluate_shadow(
            method="GET", path="/api/registry/character/x",
            legacy_auth=shadow.LegacyAuthObservation(False, "local-read", "anonymous"),
            request_id="req-4",
        )
        self.assertEqual(event["identity_state"], "legacy_anonymous")
        self.assertNotEqual(event["policy_result"], "allow")

    def test_capability_invocation_is_not_evaluated(self):
        called = []
        original = capabilities.decide_permission
        capabilities.decide_permission = lambda **kwargs: called.append(kwargs) or {"allow": True}
        try:
            event = shadow.evaluate_shadow(
                method="POST", path="/api/capabilities/invocations",
                bearer_token="shadow-legacy-key", legacy_auth=self.legacy,
            )
        finally:
            capabilities.decide_permission = original
        self.assertEqual(event["evaluation_state"], "not_evaluated")
        self.assertIsNone(event["identity_state"])
        self.assertEqual(called, [])

    def test_policy_version_is_stable_and_covers_policy_inputs(self):
        first = shadow.policy_version()
        self.assertEqual(first, shadow.policy_version())
        capabilities.set_permission(
            subject_type="*", subject="*", capability="image.generate",
            effect="allow", actor="test",
        )
        self.assertNotEqual(first, shadow.policy_version())

    def test_policy_db_failure_is_explicit_error_not_ready(self):
        capabilities.set_permission(
            subject_type="*", subject="*", capability="image.generate",
            effect="allow", actor="test",
        )
        with mock.patch.object(shadow.store, "conn", side_effect=RuntimeError("db down")):
            event = shadow.evaluate_shadow(
                method="GET", path="/api/registry/character/x",
                bearer_token="shadow-legacy-key", legacy_auth=self.legacy,
            )
        self.assertEqual(event["evaluation_state"], "error")
        self.assertEqual(event["policy_result"], "error")
        self.assertEqual(event["policy_state"], "unavailable")
        self.assertIsNone(event["policy_version"])
        self.assertEqual(event["reason"], "policy_snapshot_unavailable")

    def test_normal_evaluation_marks_policy_ready(self):
        event = shadow.evaluate_shadow(
            method="GET", path="/api/registry/character/x",
            bearer_token="shadow-legacy-key", legacy_auth=self.legacy,
        )
        self.assertEqual(event["policy_state"], "ready")
        self.assertEqual(len(event["policy_version"]), 16)

    def test_management_route_without_legacy_observation_is_insufficient(self):
        event = shadow.evaluate_shadow(
            method="GET", path="/api/registry/character/x",
            bearer_token="shadow-legacy-key",
        )
        self.assertEqual(event["evaluation_state"], "insufficient_context")
        self.assertEqual(event["policy_result"], "insufficient_context")


if __name__ == "__main__":
    unittest.main(verbosity=2)
