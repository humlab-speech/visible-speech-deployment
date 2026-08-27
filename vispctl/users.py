"""VISP user management - MongoDB user CRUD operations.

VISP has two parallel role systems (see AGENTS.md). This module deals only with
the system level: ``users.system_role`` is either ``sys_admin`` (super user,
can reach the admin panel and create projects) or ``user`` (everyone else).

Project-level roles live on ``projects.members[].role`` and are managed from the
web UI by a project's admins, not from here.
"""

import json

from .exceptions import UserError
from .mongo import js_escape, mongosh_json
from .runner import Colors
from .runner import color as _color

COLLECTION = "users"

SYSTEM_ROLE_SYS_ADMIN = "sys_admin"
SYSTEM_ROLE_USER = "user"
VALID_SYSTEM_ROLES = [SYSTEM_ROLE_SYS_ADMIN, SYSTEM_ROLE_USER]


def _system_role(user: dict) -> str:
    """Read a user's system role, failing closed on anything unrecognised."""
    return SYSTEM_ROLE_SYS_ADMIN if user.get("system_role") == SYSTEM_ROLE_SYS_ADMIN else SYSTEM_ROLE_USER


def _validate_system_role(role: str) -> None:
    if role not in VALID_SYSTEM_ROLES:
        raise UserError(f"Invalid system role: {role}. Valid: {', '.join(VALID_SYSTEM_ROLES)}")


# -- Commands -----------------------------------------------------------------


def cmd_list(args) -> None:  # noqa: ARG001
    """List all users."""
    users = mongosh_json(
        f"db.{COLLECTION}.find({{}}, {{username: 1, fullName: 1, email: 1, loginAllowed: 1, system_role: 1}}).toArray()"
    )

    if not users:
        print("No users found.")
        return

    print(_color(f"{'Username':<35} {'Name':<25} {'Active':<8} {'System role'}", Colors.CYAN))
    print("-" * 100)

    for user in users:
        username = user.get("username", "N/A")[:34]
        name = user.get("fullName", "N/A")[:24]
        active = _color("Yes", Colors.GREEN) if user.get("loginAllowed") else _color("No", Colors.RED)
        role = _system_role(user)
        role_str = _color(role, Colors.YELLOW) if role == SYSTEM_ROLE_SYS_ADMIN else role

        print(f"{username:<35} {name:<25} {active:<17} {role_str}")


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
    role = _system_role(user)
    role_str = _color(role, Colors.YELLOW) if role == SYSTEM_ROLE_SYS_ADMIN else role
    print(f"  {'System Role:':<20} {role_str}")
    print()

    memberships = mongosh_json(
        f"db.projects.find({{'members.username': '{js_escape(username)}'}}, {{id: 1, name: 1, members: 1}}).toArray()"
    ) or []
    print(_color("  Project roles:", Colors.YELLOW))
    if memberships:
        for project in memberships:
            member = next(
                (m for m in project.get("members", []) if m.get("username") == username),
                {},
            )
            project_role = member.get("role") or "researcher"
            print(f"    {project.get('name', project.get('id'))}: {project_role}")
    else:
        print("    (not a member of any project)")


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
        "system_role": SYSTEM_ROLE_SYS_ADMIN if getattr(args, "sys_admin", False) else SYSTEM_ROLE_USER,
    }

    result = mongosh_json(f"db.{COLLECTION}.insertOne({json.dumps(user_doc)})")

    if result and result.get("acknowledged"):
        print(_color(f"Created user: {username}", Colors.GREEN))
        print(f"  Email: {email}")
        print(f"  System role: {user_doc['system_role']}")
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


def cmd_set_system_role(args) -> None:
    """Set a user's system role (sys_admin or user)."""
    username = args.username
    role = args.role
    _validate_system_role(role)

    escaped_username = js_escape(username)

    # Never leave the installation without a super user: demoting the last
    # sys_admin would lock everyone out of the admin panel and project creation.
    if role != SYSTEM_ROLE_SYS_ADMIN:
        current = mongosh_json(f"db.{COLLECTION}.findOne({{username: '{escaped_username}'}})")
        if current and _system_role(current) == SYSTEM_ROLE_SYS_ADMIN:
            remaining = mongosh_json(
                f"db.{COLLECTION}.countDocuments({{system_role: '{SYSTEM_ROLE_SYS_ADMIN}', "
                f"username: {{$ne: '{escaped_username}'}}}})"
            )
            if not remaining:
                raise UserError(f"{username} is the last sys_admin - promote another user first")

    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{escaped_username}'}}, {{$set: {{system_role: '{role}'}}}})"
    )

    if not result or result.get("matchedCount", 0) == 0:
        raise UserError(f"User not found: {username}")
    elif result.get("modifiedCount", 0) > 0:
        print(_color(f"Set system role of {username} to {role}", Colors.GREEN))
    else:
        print(f"User {username} already has system role {role}")


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


# -- Dispatch map (used by visp.py cmd_users) ---------------------------------

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
    "set-system-role": cmd_set_system_role,
    "delete": cmd_delete,
    "rm": cmd_delete,
}
