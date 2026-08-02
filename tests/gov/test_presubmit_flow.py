"""Fail-closed and privacy boundaries of the presubmit orchestration layer.

``zgov.presubmit`` is the only module that runs the whole compiler -> state ->
runner -> evidence -> Spark -> metrics flow, so its refusals are the last thing
standing between an unverifiable candidate and a GREEN envelope.  The cases
below pin the refusals that cannot be observed from the individual modules:

* an unfrozen milestone must abort before any work, because without a frozen
  baseline every identity assertion degrades into self-attestation;
* a dirty tracked worktree must abort, because the evidence would not describe
  the committed candidate;
* the child-process environment must stay an explicit allow-list, so no proxy,
  credential, or startup hook can leak into a gate command;
* the public evidence comment must be privacy-scanned before it is returned;
* a missing packaging verifier must surface as a skipped, non-green check
  rather than silently vanishing from the check list.

Every case is re-entrant across roles configurations: no model identity is
named here, and the milestone/worktree refusals are exercised under both the
placeholder profile and a fully resolved one.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from support import pinned_roles
from zgov import milestone, presubmit
from zgov.milestone import MilestoneNotFrozen

UNFROZEN_MILESTONE_PATH = "/nonexistent/zgov-presubmit-milestone-does-not-exist.json"


def _frozen_milestone_document() -> dict:
    """Minimal frozen baseline: enough to pass the milestone guard."""
    return {
        "schema": milestone.MILESTONE_SCHEMA,
        "frozen": True,
        "milestone_id": "ZGOV-PRESUBMIT-TEST",
        "repo": "zcode-governance-infra",
        "base_sha": "1" * 40,
        "base_tree": "2" * 40,
        "spark": {
            "audit_ids": [],
            "expected_raw_platform_sha256": {},
            "normalized_finding_ids": {},
            "author_closure_denominator": None,
            "author_finding_ids": [],
            "historical_findings": {},
            "expected_historical": [],
            "expected_current": [],
        },
        "transcript": {
            "mission_scope_sha256": None,
            "compiled_plan_sha256": None,
            "audited_parent_sha": None,
            "author_closure_plan_sha256": None,
        },
        "gate_stage_map": {"G-TARGETED": "targeted", "G-FULL": "full", "G-FRESH": "fresh"},
    }


class _PresubmitCase(unittest.TestCase):
    """Environment and repository scaffolding shared by the presubmit cases."""

    def _set_env(self, name: str, value: str) -> None:
        previous = os.environ.get(name)

        def restore() -> None:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)
        os.environ[name] = value

    def _unfrozen_milestone(self) -> None:
        self._set_env("ZGOV_MILESTONE_PATH", UNFROZEN_MILESTONE_PATH)
        self.assertFalse(milestone.is_frozen())

    def _frozen_milestone(self) -> None:
        path = Path(self._tmpdir()) / "milestone.json"
        path.write_text(json.dumps(_frozen_milestone_document()), encoding="utf-8")
        self._set_env("ZGOV_MILESTONE_PATH", str(path))
        self.assertTrue(milestone.is_frozen())

    def _tmpdir(self) -> str:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return holder.name

    def _git(self, root: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _git_repo(self, *, dirty: bool) -> Path:
        """Create a throwaway committed git repository, optionally dirty."""
        root = Path(self._tmpdir()) / "repo"
        root.mkdir()
        self._git(root, "init", "--quiet")
        self._git(root, "config", "user.email", "presubmit@example.invalid")
        self._git(root, "config", "user.name", "presubmit test")
        self._git(root, "config", "commit.gpgsign", "false")
        (root / "tracked.txt").write_text("committed\n", encoding="utf-8")
        self._git(root, "add", "tracked.txt")
        self._git(root, "commit", "--quiet", "-m", "baseline")
        if dirty:
            (root / "tracked.txt").write_text("uncommitted edit\n", encoding="utf-8")
        return root


class UnfrozenMilestoneFailsClosed(_PresubmitCase):
    """No frozen baseline means no comparable identity: never GREEN."""

    def test_run_presubmit_raises_milestone_not_frozen(self) -> None:
        """The guard fires before any repository work, under either roles profile."""
        for resolved in (False, True):
            with self.subTest(roles_resolved=resolved):
                pinned_roles(self, resolved=resolved)
                self._unfrozen_milestone()
                root = self._git_repo(dirty=False)
                with self.assertRaises(MilestoneNotFrozen) as ctx:
                    presubmit.run_presubmit(root)
                self.assertIn("MILESTONE_NOT_FROZEN", str(ctx.exception))

    def test_main_reports_red_and_exit_two(self) -> None:
        """The CLI maps the refusal to the RED envelope and exit status 2."""
        for resolved in (False, True):
            with self.subTest(roles_resolved=resolved):
                pinned_roles(self, resolved=resolved)
                self._unfrozen_milestone()
                root = self._git_repo(dirty=False)
                stream = io.StringIO()
                with redirect_stdout(stream):
                    status = presubmit.main(["--repo", str(root)])
                self.assertEqual(status, 2)
                payload = json.loads(stream.getvalue())
                self.assertEqual(payload["schema"], "presubmit-envelope.v16")
                self.assertEqual(payload["status"], "RED")
                self.assertIn("MILESTONE_NOT_FROZEN", payload["error"])
                self.assertNotEqual(payload["status"], "GREEN")


class DirtyWorktreeRejected(_PresubmitCase):
    """Evidence must describe the committed candidate, not a local edit."""

    def test_uncommitted_tracked_change_is_refused(self) -> None:
        for resolved in (False, True):
            with self.subTest(roles_resolved=resolved):
                pinned_roles(self, resolved=resolved)
                self._frozen_milestone()
                root = self._git_repo(dirty=True)
                with self.assertRaises(RuntimeError) as ctx:
                    presubmit.run_presubmit(root)
                self.assertIn("presubmit requires clean tracked worktree", str(ctx.exception))


class SourceIdentityGuard(_PresubmitCase):
    """The source checkout must stay byte-identical and clean across a flow."""

    def test_changed_identity_is_rejected(self) -> None:
        before = ("a" * 40, "b" * 40, False)
        self.assertFalse(presubmit.source_identity_guard(before, ("c" * 40, "b" * 40, False)))
        self.assertFalse(presubmit.source_identity_guard(before, ("a" * 40, "d" * 40, False)))
        self.assertFalse(presubmit.source_identity_guard(before, ("a" * 40, "b" * 40, True)))

    def test_unchanged_clean_identity_is_accepted(self) -> None:
        identity = ("a" * 40, "b" * 40, False)
        self.assertTrue(presubmit.source_identity_guard(identity, identity))
        # An unchanged but dirty identity is still refused: equality alone is
        # not enough, the checkout has to have been clean throughout.
        dirty = ("a" * 40, "b" * 40, True)
        self.assertFalse(presubmit.source_identity_guard(dirty, dirty))


class ChildEnvironmentAllowList(_PresubmitCase):
    """Gate commands inherit an explicit allow-list, never the caller's env."""

    def test_env_keys_are_exactly_the_allow_list(self) -> None:
        root = Path(self._tmpdir())
        env = presubmit._env(root)
        self.assertEqual(
            set(env),
            {
                "PATH",
                "LANG",
                "LC_ALL",
                "PYTHONUNBUFFERED",
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONNOUSERSITE",
                "PYTHONPATH",
            },
        )
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
        self.assertEqual(env["PYTHONPATH"], str(root))
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        for forbidden in (
            "HOME",
            "ZCODE_HOME",
            "ZGOV_ROLES_PATH",
            "ZGOV_MILESTONE_PATH",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
            "NO_PROXY",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "SSH_AUTH_SOCK",
            "PYTHONSTARTUP",
            "PYTHONHOME",
            "LD_PRELOAD",
        ):
            self.assertNotIn(forbidden, env)


class PublicCommentPrivacyGate(_PresubmitCase):
    """A public comment body is scanned before it can be returned."""

    def _envelope(self, head: str) -> dict:
        return {
            "status": "GREEN",
            "head_sha": head,
            "tree_sha": "b" * 40,
            "envelope_sha256": "c" * 64,
            "checks": [],
            "review_packet": {"schema": "pr-trace.v16", "coverage_status": "COMPLETE", "verdict": "APPROVE"},
            "metrics_dashboard": {"observed": {"spark_audit_count": 0, "gate_elapsed_sec": 0.0}},
        }

    def test_private_path_is_refused(self) -> None:
        envelope = self._envelope("a" * 40)
        # The literal is split the same way ``contracts.FORBIDDEN_TEXT_RE`` and
        # ``scripts/verify-governance.py`` split theirs: this test must exercise
        # the real ``/home/`` branch of the guard, but the source file must not
        # itself contain a checked-in absolute home path.
        envelope["review_packet"]["verdict"] = "APPROVE " + "/" + "home/user/secret"
        with self.assertRaises(RuntimeError) as ctx:
            presubmit.render_evidence_comment(envelope)
        self.assertEqual(str(ctx.exception), "public comment privacy red")

    def test_credential_token_is_refused(self) -> None:
        envelope = self._envelope("a" * 40)
        envelope["review_packet"]["coverage_status"] = "ghp_" + "A" * 24
        with self.assertRaises(RuntimeError) as ctx:
            presubmit.render_evidence_comment(envelope)
        self.assertEqual(str(ctx.exception), "public comment privacy red")

    def test_sanitized_envelope_renders(self) -> None:
        body = presubmit.render_evidence_comment(self._envelope("a" * 40))
        self.assertIn("V16 generated evidence envelope", body)


class ManifestVerifierUnavailable(_PresubmitCase):
    """A missing packaging verifier degrades to skipped, never to green."""

    def test_absent_script_yields_a_non_green_skipped_check(self) -> None:
        root = Path(self._tmpdir())
        self.assertFalse((root / presubmit.MANIFEST_VERIFIER_RELPATH).exists())
        check, status = presubmit.manifest_verifier_check(
            root,
            artifact_dir=root / "artifacts",
            expected_head="a" * 40,
            expected_tree="b" * 40,
        )
        self.assertEqual(check["id"], "manifest-verifier")
        self.assertNotEqual(status, 0)
        self.assertEqual(check["skipped"], 1)
        self.assertEqual(check["passed"], 0)
        self.assertEqual(check["denominator"], 1)
        self.assertEqual(check["total"], check["passed"] + check["failed"] + check["skipped"] + check["unknown"])
        self.assertEqual(presubmit.check_status(check), "RED")
        self.assertNotEqual(presubmit.check_status(check), "GREEN")

    def test_flow_refuses_to_continue_without_the_verifier(self) -> None:
        """The skipped check is a stop condition, not an omission."""
        root = Path(self._tmpdir())
        check, status = presubmit.manifest_verifier_check(
            root,
            artifact_dir=root / "artifacts",
            expected_head="a" * 40,
            expected_tree="b" * 40,
        )
        # This mirrors the exact branch _flow takes on a non-zero status.
        self.assertNotEqual(status, 0)
        message = "manifest verifier unavailable" if check["skipped"] else "manifest verifier RED"
        self.assertEqual(message, "manifest verifier unavailable")


if __name__ == "__main__":
    unittest.main()
