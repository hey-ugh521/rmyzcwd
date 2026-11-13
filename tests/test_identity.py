"""Phase 4F-1 identity data model and credential lifecycle tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from core import identity, store  # noqa: E402


class IdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "identity.db"
        store.init_db()
        identity.init_schema()
        self.service = identity.create_principal(kind="machine", display_name="QQ",
                                                 role="service", principal_id="svc-qq")

    def tearDown(self) -> None:
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_secret_hashed_and_authenticated(self):
        made = identity.create_credential(principal_id="svc-qq", kind="api_key")
        token = made["secret"]
        self.assertIn("mob_cred_", token)
        with store.conn() as c:
            row = c.execute("SELECT secret_hash FROM credentials WHERE id=?",
                            (made["credential"]["id"],)).fetchone()
        self.assertNotIn(token, row["secret_hash"])
        self.assertIn("scrypt$", row["secret_hash"])
        auth = identity.authenticate_credential(token)
        self.assertIsNotNone(auth)
        self.assertEqual(auth["principal"]["id"], "svc-qq")
        self.assertIsNone(identity.authenticate_credential(token + "x"))

    def test_revoke_and_disable_invalidate(self):
        made = identity.create_credential(principal_id="svc-qq")
        identity.revoke_credential(made["credential"]["id"])
        self.assertIsNone(identity.authenticate_credential(made["secret"]))
        made2 = identity.create_credential(principal_id="svc-qq")
        identity.disable_principal("svc-qq")
        self.assertIsNone(identity.authenticate_credential(made2["secret"]))
        with self.assertRaises(identity.IdentityError):
            identity.create_credential(principal_id="svc-qq")

    def test_expiry_and_rotation(self):
        made = identity.create_credential(principal_id="svc-qq", expires_at=time.time() - 1)
        self.assertIsNone(identity.authenticate_credential(made["secret"]))
        made2 = identity.create_credential(principal_id="svc-qq")
        rotated = identity.rotate_credential(made2["credential"]["id"])
        self.assertIsNone(identity.authenticate_credential(made2["secret"]))
        self.assertIsNotNone(identity.authenticate_credential(rotated["secret"]))
        with store.conn() as c:
            row = c.execute("SELECT rotated_from FROM credentials WHERE id=?",
                            (rotated["credential"]["id"],)).fetchone()
        self.assertEqual(row["rotated_from"], made2["credential"]["id"])

    def test_bootstrap_one_time_and_concurrent(self):
        boot = identity.bootstrap_admin(ttl=300)
        token = boot["token"]
        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            try:
                identity.consume_bootstrap_token(token)
                results.append("ok")
            except identity.IdentityError as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(len(results), 2)
        with self.assertRaises(identity.IdentityError) as cm:
            identity.consume_bootstrap_token(token)
        self.assertEqual(cm.exception.code, "token_consumed")

    def test_bootstrap_expired_and_recovery(self):
        # bootstrap_admin creates the first admin; recover targets it
        boot = identity.bootstrap_admin(ttl=300)
        identity.consume_bootstrap_token(boot["token"])
        recovery = identity.recover_admin(ttl=1)
        time.sleep(1.1)
        with self.assertRaises(identity.IdentityError) as cm:
            identity.consume_bootstrap_token(recovery["token"])
        self.assertEqual(cm.exception.code, "token_expired")

    def test_bootstrap_rejects_when_admin_exists(self):
        identity.create_principal(kind="human", display_name="Admin",
                                  role="admin", principal_id="admin-1")
        with self.assertRaises(identity.IdentityError) as cm:
            identity.bootstrap_admin(ttl=300)
        self.assertEqual(cm.exception.code, "bootstrap_exists")

    def test_admin_session_csrf_and_revoke(self):
        identity.create_principal(kind="human", display_name="Admin",
                                  role="admin", principal_id="admin-1")
        session = identity.create_admin_session(principal_id="admin-1")
        auth = identity.authenticate_admin_session(session["session_token"])
        self.assertIsNotNone(auth)
        self.assertTrue(identity.verify_csrf(session["session_id"], session["csrf_token"]))
        self.assertFalse(identity.verify_csrf(session["session_id"], "wrong"))
        identity.revoke_admin_session(session["session_id"])
        self.assertIsNone(identity.authenticate_admin_session(session["session_token"]))
        with self.assertRaises(identity.IdentityError):
            identity.create_admin_session(principal_id="svc-qq")

    def test_audit_written_in_same_operation(self):
        made = identity.create_credential(principal_id="svc-qq")
        identity.revoke_credential(made["credential"]["id"])
        with store.conn() as c:
            rows = c.execute(
                "SELECT action,actor FROM audit WHERE action LIKE ? ORDER BY ts",
                ("identity.%",)).fetchall()
        actions = [r["action"] for r in rows]
        self.assertIn("identity.credential.create", actions)
        self.assertIn("identity.credential.revoke", actions)
        self.assertTrue(all(r["actor"] for r in rows))

    def test_audit_failure_rolls_back_identity_write(self):
        from unittest import mock
        with mock.patch.object(identity, "_audit", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                identity.create_credential(principal_id="svc-qq")
        with store.conn() as c:
            count = c.execute(
                "SELECT COUNT(*) AS n FROM credentials WHERE principal_id='svc-qq'").fetchone()["n"]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
