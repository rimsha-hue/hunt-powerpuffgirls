import pytest

from app.domain.access import Account, AdminAction, Role, is_admin, refusal, should_be_admin


def _account(**changes: object) -> Account:
    values: dict[str, object] = {
        "id": 2, "role": "user", "suspended": False, "verified": True, "demo": False,
    }  # fmt: skip
    values.update(changes)
    return Account(**values)  # type: ignore[arg-type]


def _refusal(
    action: AdminAction, target: Account, admins: int = 2, role: Role | None = None
) -> str | None:
    return refusal(action, actor_id=1, target=target, active_admins=admins, new_role=role)


class TestBootstrap:
    def test_a_listed_email_becomes_admin_only_once_verified(self) -> None:
        listed = {"boss@example.com"}
        assert should_be_admin("Boss@Example.com", True, "user", listed)
        assert not should_be_admin("boss@example.com", False, "user", listed)
        assert not should_be_admin("other@example.com", True, "user", listed)
        assert not should_be_admin("boss@example.com", True, "admin", listed)

    def test_a_suspended_admin_has_no_admin_rights(self) -> None:
        assert (
            is_admin("admin", False) and not is_admin("admin", True) and not is_admin("user", False)
        )


class TestRefusals:
    @pytest.mark.parametrize("action", ["suspend", "delete"])
    def test_no_admin_can_lock_themselves_out(self, action: AdminAction) -> None:
        assert _refusal(action, _account(id=1, role="admin")) is not None

    def test_no_admin_can_drop_their_own_role(self) -> None:
        assert _refusal("set_role", _account(id=1, role="admin"), role="user") is not None

    @pytest.mark.parametrize(
        ("action", "role"), [("suspend", None), ("delete", None), ("set_role", "user")]
    )
    def test_the_last_admin_stays(self, action: AdminAction, role: Role | None) -> None:
        target = _account(role="admin")
        assert (
            _refusal(action, target, admins=1, role=role)
            == "That would leave the service without an admin."
        )
        assert _refusal(action, target, admins=2, role=role) is None

    def test_the_demo_account_cant_be_deleted(self) -> None:
        assert _refusal("delete", _account(demo=True)) is not None
        assert _refusal("suspend", _account(demo=True)) is None  # abuse can still be stopped

    def test_only_verified_accounts_become_admins(self) -> None:
        assert _refusal("set_role", _account(verified=False), role="admin") is not None
        assert _refusal("set_role", _account(), role="admin") is None

    def test_actions_that_change_nothing_are_refused(self) -> None:
        assert _refusal("suspend", _account(suspended=True)) is not None
        assert _refusal("restore", _account()) is not None
        assert _refusal("resend_verification", _account()) is not None
        assert _refusal("set_role", _account(), role="user") is not None
