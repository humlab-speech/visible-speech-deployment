#!/usr/bin/env python3
"""
VISP User Management - Manage users in MongoDB without mongo-express

Usage:
  visp-users.py list                     # List all users
  visp-users.py show <username>          # Show user details
  visp-users.py create <email>           # Create new user
  visp-users.py activate <username>      # Enable login for user
  visp-users.py deactivate <username>    # Disable login for user
  visp-users.py grant <username> <priv>  # Grant privilege (createProjects, createInviteCodes)
  visp-users.py revoke <username> <priv> # Revoke privilege
  visp-users.py delete <username>        # Delete user (with confirmation)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Configuration
ENV_FILE = Path(__file__).parent / ".env"
MONGO_CONTAINER = "mongo"  # Container name (without systemd- prefix for quadlets)
DATABASE = "visp"
COLLECTION = "users"


# Colors
class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    CYAN = "\033[0;36m"
    BOLD = "\033[1m"
    NC = "\033[0m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{Colors.NC}"


def load_env() -> dict:
    """Load environment variables from .env file."""
    env = {}
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    return env


def get_mongo_password() -> str:
    """Get MongoDB root password from .env."""
    env = load_env()
    password = env.get("MONGO_ROOT_PASSWORD") or env.get("MONGO_INITDB_ROOT_PASSWORD")
    if not password:
        print(color("Error: MONGO_ROOT_PASSWORD not found in .env", Colors.RED))
        sys.exit(1)
    return password


def find_mongo_container() -> str:
    """Find the running mongo container name."""
    # Try common container names
    for name in [MONGO_CONTAINER, f"systemd-{MONGO_CONTAINER}", "visp-mongo"]:
        result = subprocess.run(
            ["podman", "inspect", name, "--format", "{{.State.Running}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return name

    print(color("Error: MongoDB container not running", Colors.RED))
    print("Start it with: ./visp-podman.py start mongo")
    sys.exit(1)


def mongosh(js_command: str, quiet: bool = False) -> str:
    """Execute a MongoDB command via mongosh in the container."""
    password = get_mongo_password()
    container = find_mongo_container()

    cmd = [
        "podman",
        "exec",
        container,
        "mongosh",
        "-u",
        "root",
        "-p",
        password,
        "--authenticationDatabase",
        "admin",
        DATABASE,
        "--quiet" if quiet else "--eval",
        js_command if not quiet else "",
    ]

    if quiet:
        cmd = [
            "podman",
            "exec",
            container,
            "mongosh",
            "-u",
            "root",
            "-p",
            password,
            "--authenticationDatabase",
            "admin",
            "--quiet",
            DATABASE,
            "--eval",
            js_command,
        ]
    else:
        cmd = [
            "podman",
            "exec",
            container,
            "mongosh",
            "-u",
            "root",
            "-p",
            password,
            "--authenticationDatabase",
            "admin",
            DATABASE,
            "--eval",
            js_command,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(color(f"MongoDB error: {result.stderr}", Colors.RED))
        sys.exit(1)
    return result.stdout


def mongosh_json(js_command: str) -> list | dict:
    """Execute MongoDB command and parse JSON result."""
    # Wrap command to output JSON
    wrapped = f"JSON.stringify({js_command})"
    output = mongosh(wrapped)

    # Find JSON in output (skip mongosh banner lines)
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("[") or line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    # Try parsing entire output
    try:
        return json.loads(output.strip())
    except json.JSONDecodeError:
        print(color(f"Failed to parse MongoDB output: {output}", Colors.RED))
        sys.exit(1)


# === Commands ===


def cmd_list(args):
    """List all users."""
    users = mongosh_json(
        f"db.{COLLECTION}.find({{}}, {{username: 1, fullName: 1, email: 1, loginAllowed: 1, privileges: 1}}).toArray()"
    )

    if not users:
        print("No users found.")
        return

    print(
        color(
            f"{'Username':<35} {'Name':<25} {'Active':<8} {'Privileges'}", Colors.CYAN
        )
    )
    print("-" * 100)

    for user in users:
        username = user.get("username", "N/A")[:34]
        name = user.get("fullName", "N/A")[:24]
        active = (
            color("Yes", Colors.GREEN)
            if user.get("loginAllowed")
            else color("No", Colors.RED)
        )

        privs = user.get("privileges", {})
        priv_list = [k for k, v in privs.items() if v]
        priv_str = ", ".join(priv_list) if priv_list else "-"

        print(f"{username:<35} {name:<25} {active:<17} {priv_str}")


def cmd_show(args):
    """Show detailed user info."""
    username = args.username
    user = mongosh_json(f"db.{COLLECTION}.findOne({{username: '{username}'}})")

    if not user:
        print(color(f"User not found: {username}", Colors.RED))
        sys.exit(1)

    print(color(f"=== User: {username} ===", Colors.CYAN))
    print()
    print(f"  {'Full Name:':<20} {user.get('fullName', 'N/A')}")
    print(f"  {'Email:':<20} {user.get('email', 'N/A')}")
    print(f"  {'EPPN:':<20} {user.get('eppn', 'N/A')}")
    login_status = (
        color("Yes", Colors.GREEN)
        if user.get("loginAllowed")
        else color("No", Colors.RED)
    )
    print(f"  {'Login Allowed:':<20} {login_status}")
    print()
    print(color("  Privileges:", Colors.YELLOW))
    privs = user.get("privileges", {})
    if privs:
        for k, v in privs.items():
            status = color("✓", Colors.GREEN) if v else color("✗", Colors.RED)
            print(f"    {status} {k}")
    else:
        print("    (none)")


def cmd_create(args):
    """Create a new user."""
    email = args.email

    # Generate username from email (replace @ and . with _)
    username = email.replace("@", "_at_").replace(".", "_dot_")

    # Check if user exists
    existing = mongosh_json(f"db.{COLLECTION}.findOne({{email: '{email}'}})")
    if existing:
        print(color(f"User with email {email} already exists", Colors.YELLOW))
        print(f"Username: {existing.get('username')}")
        return

    # Create user document
    user_doc = {
        "firstName": args.first_name or "New",
        "lastName": args.last_name or "User",
        "fullName": f"{args.first_name or 'New'} {args.last_name or 'User'}",
        "email": email,
        "eppn": email,
        "username": username,
        "loginAllowed": True,
        "privileges": {
            "createProjects": args.can_create_projects,
            "createInviteCodes": False,
        },
    }

    js_doc = json.dumps(user_doc)
    result = mongosh_json(f"db.{COLLECTION}.insertOne({js_doc})")

    if result.get("acknowledged"):
        print(color(f"Created user: {username}", Colors.GREEN))
        print(f"  Email: {email}")
        print(f"  Can create projects: {args.can_create_projects}")
    else:
        print(color("Failed to create user", Colors.RED))


def cmd_activate(args):
    """Enable login for user."""
    username = args.username
    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{username}'}}, {{$set: {{loginAllowed: true}}}})"
    )

    if result.get("matchedCount", 0) == 0:
        print(color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(color(f"Activated user: {username}", Colors.GREEN))
    else:
        print(f"User {username} was already active")


def cmd_deactivate(args):
    """Disable login for user."""
    username = args.username
    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{username}'}}, {{$set: {{loginAllowed: false}}}})"
    )

    if result.get("matchedCount", 0) == 0:
        print(color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(color(f"Deactivated user: {username}", Colors.YELLOW))
    else:
        print(f"User {username} was already inactive")


def cmd_grant(args):
    """Grant a privilege to user."""
    username = args.username
    privilege = args.privilege

    valid_privs = ["createProjects", "createInviteCodes"]
    if privilege not in valid_privs:
        print(color(f"Invalid privilege: {privilege}", Colors.RED))
        print(f"Valid privileges: {', '.join(valid_privs)}")
        sys.exit(1)

    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{username}'}}, {{$set: {{'privileges.{privilege}': true}}}})"
    )

    if result.get("matchedCount", 0) == 0:
        print(color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(color(f"Granted {privilege} to {username}", Colors.GREEN))
    else:
        print(f"User {username} already has {privilege}")


def cmd_revoke(args):
    """Revoke a privilege from user."""
    username = args.username
    privilege = args.privilege

    valid_privs = ["createProjects", "createInviteCodes"]
    if privilege not in valid_privs:
        print(color(f"Invalid privilege: {privilege}", Colors.RED))
        print(f"Valid privileges: {', '.join(valid_privs)}")
        sys.exit(1)

    result = mongosh_json(
        f"db.{COLLECTION}.updateOne({{username: '{username}'}}, {{$set: {{'privileges.{privilege}': false}}}})"
    )

    if result.get("matchedCount", 0) == 0:
        print(color(f"User not found: {username}", Colors.RED))
    elif result.get("modifiedCount", 0) > 0:
        print(color(f"Revoked {privilege} from {username}", Colors.YELLOW))
    else:
        print(f"User {username} didn't have {privilege}")


def cmd_delete(args):
    """Delete a user."""
    username = args.username

    # Show user first
    user = mongosh_json(f"db.{COLLECTION}.findOne({{username: '{username}'}})")
    if not user:
        print(color(f"User not found: {username}", Colors.RED))
        sys.exit(1)

    print("About to delete user:")
    print(f"  Username: {username}")
    print(f"  Name: {user.get('fullName')}")
    print(f"  Email: {user.get('email')}")
    print()

    if not args.force:
        confirm = input(color("Are you sure? Type 'yes' to confirm: ", Colors.YELLOW))
        if confirm.lower() != "yes":
            print("Cancelled.")
            return

    result = mongosh_json(f"db.{COLLECTION}.deleteOne({{username: '{username}'}})")

    if result.get("deletedCount", 0) > 0:
        print(color(f"Deleted user: {username}", Colors.GREEN))
    else:
        print(color("Failed to delete user", Colors.RED))


# === Main ===


def main():
    parser = argparse.ArgumentParser(
        description="VISP User Management - Manage users in MongoDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  visp-users.py list                              # List all users
  visp-users.py show testuser_at_example_dot_com  # Show user details
  visp-users.py create user@example.com           # Create new user
  visp-users.py activate testuser_at_example_dot_com
  visp-users.py deactivate testuser_at_example_dot_com
  visp-users.py grant testuser_at_example_dot_com createProjects
  visp-users.py revoke testuser_at_example_dot_com createProjects

Privileges:
  createProjects    - Can create new projects
  createInviteCodes - Can create invite codes for other users
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # list
    subparsers.add_parser("list", aliases=["ls"], help="List all users")

    # show
    p_show = subparsers.add_parser("show", aliases=["get"], help="Show user details")
    p_show.add_argument("username", help="Username to show")

    # create
    p_create = subparsers.add_parser("create", aliases=["add"], help="Create new user")
    p_create.add_argument("email", help="User email address")
    p_create.add_argument("--first-name", "-f", help="First name")
    p_create.add_argument("--last-name", "-l", help="Last name")
    p_create.add_argument(
        "--can-create-projects",
        "-p",
        action="store_true",
        help="Allow user to create projects",
    )

    # activate
    p_activate = subparsers.add_parser(
        "activate", aliases=["enable"], help="Enable user login"
    )
    p_activate.add_argument("username", help="Username to activate")

    # deactivate
    p_deactivate = subparsers.add_parser(
        "deactivate", aliases=["disable"], help="Disable user login"
    )
    p_deactivate.add_argument("username", help="Username to deactivate")

    # grant
    p_grant = subparsers.add_parser("grant", help="Grant privilege to user")
    p_grant.add_argument("username", help="Username")
    p_grant.add_argument(
        "privilege", help="Privilege to grant (createProjects, createInviteCodes)"
    )

    # revoke
    p_revoke = subparsers.add_parser("revoke", help="Revoke privilege from user")
    p_revoke.add_argument("username", help="Username")
    p_revoke.add_argument("privilege", help="Privilege to revoke")

    # delete
    p_delete = subparsers.add_parser("delete", aliases=["rm"], help="Delete user")
    p_delete.add_argument("username", help="Username to delete")
    p_delete.add_argument(
        "--force", "-f", action="store_true", help="Skip confirmation"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    cmd_map = {
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

    handler = cmd_map.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
