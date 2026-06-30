"""VISP user management — MongoDB user CRUD operations."""

import json

from .exceptions import UserError
from .mongo import js_escape, mongosh_json
from .runner import Colors
from .runner import color as _color

COLLECTION = "users"
VALID_PRIVILEGES = ["createProjects", "createInviteCodes"]


def _validate_privilege(privilege: str) -> None:
    if privilege not in VALID_PRIVILEGES:
        raise UserError(f"Invalid privilege: {privilege}. Valid: {', '.join(VALID_PRIVILEGES)}")


# ── Commands ───────────────────────────────────────────────────────────────────


def cmd_list(args) -> None:  # noqa: ARG001
    """List all users."""
    users = mongosh_json(
        f"db.{COLLECTION}.find({{}}, {{username: 1, fullName: 1, email: 1, loginAllowed: 1, privileges: 1}}).toArray()"
    )

    if not users:
        print("No users found.")
        return

    print(_color(f"{'Username':<35} {'Name':<25} {'Active':<8} {'Privileges'}", Colors.CYAN))
    print("-" * 100)

    for user in users:
        username = user.get("username", "N/A")[:34]
        name = user.get("fullName", "N/A")[:24]
        active = _color("Yes", Colors.GREEN) if user.get("loginAllowed") else _color("No", Colors.RED)

        privs = user.get("privileges", {})
        priv_list = [k for k, v in privs.items() if v]
        priv_str = ", ".join(priv_list) if priv_list else "-"

        print(f"{username:<35} {name:<25} {active:<17} {priv_str}")


def cmd_show(args) -> None:
    """Show detailed user info."""
    username = args.username
    user = mongosh_json(f"db.{COLLECTION}.findOne({{username: '{js_escape(username)}'}})")

    if not user:
        raise UserError(f"User not found: {username}")

    print(_color(f"=== User: {username} ===", Colors.CYAN))
    print()
    print(f"  {'Full Name:':<20} {user.get('fullName', 'N/A')}")
    print(f"  {'Email:':<20} {user.get('email', 'N/A')}")
    print(f"  {'EPPN:':<20} {user.get('eppn', 'N/A')}")
    login_status = _color("Yes", Colors.GREEN) if user.get("loginAllowed") else _color("No", Colors.RED)
    print(f"  {'Login Allowed:':<20} {login_status}")
    print()
    print(_color("  Privileges:", Colors.YELLOW))
    privs = user.get("privileges", {})
    if privs:
        for k, v in privs.items():
            status = _color("✓", Colors.GREEN) if v else _color("✗", Colors.RED)
            print(f"    {status} {k}")
    else:
        print("    (none)")


def cmd_create(args) -> None:
    """Create a new user."""
    email = args.email
    username = email.replace("@", "_at_").replace(".", "_dot_")

    existing = mongosh_json(f"db.{COLLECTION}.findOne({{email: '{js_escape(email)}'}})")
    if existing:
        print(_color(f"User with email {email} already exists", Colors.YELLOW))
        print(f"Username: {existing.get('username')}")
        return

    user_doc = {
        "firstName": args.first_name or "New",
        "lastName": args.last_name or "User",
        "fullName": f"{args.first_name or 'New'} {args.last_name or 'User'}",
        "email": email,
        "eppn": email,
        "username": username,
        "loginAllowed": True,
        "privileges": {
            "createProjects": getattr(args, "can_create_projects", False),
            "createInviteCodes": False,
        },
    }

    result = mongosh_json(f"db.{COLLECTION}.insertOne({json.dumps(user_doc)})")

    if result and result.get("acknowledged"):
        print(_color(f"Created user: {username}", Colors.GREEN))
        print(f"  Email: {email}")
        print(f"  Can create projects: {getattr(args, 'can_create_projects', False)}")
    else:
        print(_color("Failed to create user", Colors.RED))


def cmd_activate(args) -> None:
    """Enable login for user."""
    username = args.username
    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{js_escape(username)}'}}, {{$set: {{loginAllowed: true}}}})"
    )

    if not result or result.get("matchedCount", 0) == 0:
        print(_color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(_color(f"Activated user: {username}", Colors.GREEN))
    else:
        print(f"User {username} was already active")


def cmd_deactivate(args) -> None:
    """Disable login for user."""
    username = args.username
    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{js_escape(username)}'}}, {{$set: {{loginAllowed: false}}}})"
    )

    if not result or result.get("matchedCount", 0) == 0:
        print(_color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(_color(f"Deactivated user: {username}", Colors.YELLOW))
    else:
        print(f"User {username} was already inactive")


def cmd_grant(args) -> None:
    """Grant a privilege to user."""
    username = args.username
    privilege = args.privilege
    _validate_privilege(privilege)

    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{js_escape(username)}'}}, {{$set: {{'privileges.{js_escape(privilege)}': true}}}})"
    )

    if not result or result.get("matchedCount", 0) == 0:
        print(_color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(_color(f"Granted {privilege} to {username}", Colors.GREEN))
    else:
        print(f"User {username} already has {privilege}")


def cmd_revoke(args) -> None:
    """Revoke a privilege from user."""
    username = args.username
    privilege = args.privilege
    _validate_privilege(privilege)

    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{js_escape(username)}'}}, {{$set: {{'privileges.{js_escape(privilege)}': false}}}})"
    )

    if not result or result.get("matchedCount", 0) == 0:
        print(_color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(_color(f"Revoked {privilege} from {username}", Colors.YELLOW))
    else:
        print(f"User {username} didn't have {privilege}")


def cmd_delete(args) -> None:
    """Delete a user."""
    username = args.username

    user = mongosh_json(f"db.{COLLECTION}.findOne({{username: '{js_escape(username)}'}})")
    if not user:
        raise UserError(f"User not found: {username}")

    print("About to delete user:")
    print(f"  Username: {username}")
    print(f"  Name: {user.get('fullName')}")
    print(f"  Email: {user.get('email')}")
    print()

    if not getattr(args, "force", False):
        confirm = input(_color("Are you sure? Type 'yes' to confirm: ", Colors.YELLOW))
        if confirm.lower() != "yes":
            print("Cancelled.")
            return

    result = mongosh_json(f"db.{COLLECTION}.deleteOne({{username: '{js_escape(username)}'}})")

    if result and result.get("deletedCount", 0) > 0:
        print(_color(f"Deleted user: {username}", Colors.GREEN))
    else:
        print(_color("Failed to delete user", Colors.RED))


# ── Dispatch map (used by visp.py cmd_users) ───────────────────────────

COMMANDS: dict = {
    "list": cmd_list,
    "ls": cmd_list,
    "show": cmd_show,
    "get": cmd_show,
    "create": cmd_create,
    "add": cmd_create,
    "activate": cmd_activate,
    "enable": cmd_activate,
    "deactivate": cmd_deactivate,
    "disable": cmd_deactivate,
    "grant": cmd_grant,
    "revoke": cmd_revoke,
    "delete": cmd_delete,
    "rm": cmd_delete,
}
