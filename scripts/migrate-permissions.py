#!/usr/bin/env python3
"""Migrate a VISP database to the two-tier (system + project) role model.

Before
------
  roles          one collection mixing system and project concerns
  users          { privileges: {sysAdmin, createProjects, createInviteCodes},
                   role: 'sys_admin' | 'project_admin' | 'researcher' }
  projects       { members: [{username}] }              — no per-member role
  invite_codes   { projectIds: [...], role }            — project optional

After
-----
  system_roles   sys_admin | user          — installation-wide
  project_roles  project_admin | researcher — within one project
  users          { system_role: 'sys_admin' | 'user' }
  projects       { members: [{username, role}] }
  invite_codes   { projectId, role, eppn }  — project and role always set

Conversion rules
----------------
  * A user is a sys_admin if their old ``privileges.sysAdmin`` was true or their
    old ``role`` was ``sys_admin``/``sysadmin``. Everyone else becomes ``user``.
    The old ``createProjects``/``createInviteCodes`` privileges do not survive:
    project creation is now sysadmin-only, and issuing invite codes is a
    project-level right granted by the ProjectAdmin role.
  * Within each project the first member — which ``createProject`` always wrote as
    the project's creator — becomes ProjectAdmin; everyone else becomes
    Researcher. No project is left without an admin.
  * Invite codes keep their first assigned project. Codes with no project cannot
    be redeemed into a project under the new model and are reported (use
    --delete-orphan-invite-codes to remove them).

The script is idempotent: members and codes that already carry valid values are
left alone, so it can be re-run safely, including against an already-migrated
database.

Usage:
  ./scripts/migrate-permissions.py                 # dry run (no changes)
  ./scripts/migrate-permissions.py --apply         # perform the migration
  ./scripts/migrate-permissions.py --apply --delete-orphan-invite-codes
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vispctl.mongo import mongosh_json  # noqa: E402

SYSTEM_ROLE_SYS_ADMIN = "sys_admin"
SYSTEM_ROLE_USER = "user"
PROJECT_ROLE_PROJECT_ADMIN = "project_admin"
PROJECT_ROLE_RESEARCHER = "researcher"

# Must stay in sync with ApiServer.DEFAULT_SYSTEM_ROLES / DEFAULT_PROJECT_ROLES in
# external/session-manager/src/ApiServer.class.js. The session-manager re-seeds
# both collections on every boot, so a drift here self-corrects on next start —
# seeding them now just means the database is already correct beforehand.
SYSTEM_ROLES = [
    {
        "name": SYSTEM_ROLE_SYS_ADMIN,
        "label": "System admin",
        "permissions": {"sysAdminPanel": True, "createProjects": True},
    },
    {
        "name": SYSTEM_ROLE_USER,
        "label": "User",
        "permissions": {"sysAdminPanel": False, "createProjects": False},
    },
]

PROJECT_ROLES = [
    {
        "name": PROJECT_ROLE_PROJECT_ADMIN,
        "label": "Project admin",
        "grantableViaInviteCode": True,
        "permissions": {
            "createInviteCodes": True,
            "manageProjectMembers": True,
            "editProjectFiles": True,
        },
    },
    {
        "name": PROJECT_ROLE_RESEARCHER,
        "label": "Researcher",
        "grantableViaInviteCode": True,
        "permissions": {
            "createInviteCodes": False,
            "manageProjectMembers": True,
            "editProjectFiles": True,
        },
    },
]

# Historic invite-code role names, including the pre-role-system labels.
INVITE_ROLE_MAP = {
    PROJECT_ROLE_PROJECT_ADMIN: PROJECT_ROLE_PROJECT_ADMIN,
    PROJECT_ROLE_RESEARCHER: PROJECT_ROLE_RESEARCHER,
    "sys_admin": PROJECT_ROLE_PROJECT_ADMIN,
    "sysadmin": PROJECT_ROLE_PROJECT_ADMIN,
    "admin": PROJECT_ROLE_PROJECT_ADMIN,
    "analyzer": PROJECT_ROLE_RESEARCHER,
    "transcriber": PROJECT_ROLE_RESEARCHER,
    "member": PROJECT_ROLE_RESEARCHER,
}


class C:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    BOLD = "\033[1m"
    NC = "\033[0m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{C.NC}"


def build_migration_js(apply_changes: bool, delete_orphan_codes: bool) -> str:
    """Build the mongosh program. Returns a JSON summary of what it did/would do."""
    return f"""
(function () {{
    const APPLY = {json.dumps(apply_changes)};
    const DELETE_ORPHAN_CODES = {json.dumps(delete_orphan_codes)};
    const SYSTEM_ROLES = {json.dumps(SYSTEM_ROLES)};
    const PROJECT_ROLES = {json.dumps(PROJECT_ROLES)};
    const INVITE_ROLE_MAP = {json.dumps(INVITE_ROLE_MAP)};
    const SYS_ADMIN = {json.dumps(SYSTEM_ROLE_SYS_ADMIN)};
    const PLAIN_USER = {json.dumps(SYSTEM_ROLE_USER)};
    const PROJECT_ADMIN = {json.dumps(PROJECT_ROLE_PROJECT_ADMIN)};
    const RESEARCHER = {json.dumps(PROJECT_ROLE_RESEARCHER)};

    const summary = {{
        collections: {{ renamedRolesToSystemRoles: false, droppedLegacyRoles: false }},
        systemRoles: {{ seeded: 0, removed: 0 }},
        projectRoles: {{ seeded: 0, removed: 0 }},
        users: {{ total: 0, toSysAdmin: 0, toUser: 0, alreadyCorrect: 0, legacyFieldsCleared: 0 }},
        projects: {{ total: 0, changed: 0, membersAssigned: 0, adminsPromoted: 0 }},
        inviteCodes: {{ total: 0, changed: 0, projectIdMigrated: 0, roleRemapped: 0,
                        eppnInitialised: 0, orphaned: 0, orphansDeleted: 0 }},
        warnings: [],
    }};

    const names = db.getCollectionNames();

    // ── 1. roles -> system_roles ─────────────────────────────────────────────
    if (names.indexOf("roles") !== -1) {{
        if (names.indexOf("system_roles") === -1) {{
            if (APPLY) {{ db.roles.renameCollection("system_roles"); }}
            summary.collections.renamedRolesToSystemRoles = true;
        }} else {{
            // Both exist: a previous run already renamed, and the app recreated
            // `roles`, or the rename was done by hand. system_roles wins.
            if (APPLY) {{ db.roles.drop(); }}
            summary.collections.droppedLegacyRoles = true;
        }}
    }}

    // ── 2. seed both role-definition collections ─────────────────────────────
    const seed = function (collectionName, roleDefs, counter) {{
        const validNames = roleDefs.map(function (r) {{ return r.name; }});
        roleDefs.forEach(function (roleDef) {{
            const existing = db.getCollection(collectionName).findOne({{ name: roleDef.name }});
            if (!existing || JSON.stringify(existing.permissions) !== JSON.stringify(roleDef.permissions)
                || existing.label !== roleDef.label) {{
                if (APPLY) {{
                    db.getCollection(collectionName).updateOne(
                        {{ name: roleDef.name }}, {{ $set: roleDef }}, {{ upsert: true }});
                }}
                counter.seeded++;
            }}
        }});
        const stale = db.getCollection(collectionName).countDocuments({{ name: {{ $nin: validNames }} }});
        if (stale > 0) {{
            if (APPLY) {{ db.getCollection(collectionName).deleteMany({{ name: {{ $nin: validNames }} }}); }}
            counter.removed = stale;
        }}
    }};
    seed("system_roles", SYSTEM_ROLES, summary.systemRoles);
    seed("project_roles", PROJECT_ROLES, summary.projectRoles);

    // ── 3. users: privileges/role -> system_role ─────────────────────────────
    db.users.find({{}}).forEach(function (user) {{
        summary.users.total++;

        const wasSysAdmin =
            user.system_role === SYS_ADMIN ||
            user.role === SYS_ADMIN ||
            user.role === "sysadmin" ||
            (user.privileges && user.privileges.sysAdmin === true);
        const targetRole = wasSysAdmin ? SYS_ADMIN : PLAIN_USER;

        const hasLegacyFields =
            typeof user.privileges !== "undefined" || typeof user.role !== "undefined";
        const roleNeedsUpdate = user.system_role !== targetRole;

        if (!roleNeedsUpdate && !hasLegacyFields) {{
            summary.users.alreadyCorrect++;
            return;
        }}

        if (roleNeedsUpdate) {{
            if (targetRole === SYS_ADMIN) {{ summary.users.toSysAdmin++; }}
            else {{ summary.users.toUser++; }}
        }} else {{
            summary.users.alreadyCorrect++;
        }}
        if (hasLegacyFields) {{ summary.users.legacyFieldsCleared++; }}

        if (APPLY) {{
            db.users.updateOne(
                {{ _id: user._id }},
                {{ $set: {{ system_role: targetRole }}, $unset: {{ privileges: "", role: "" }} }});
        }}
    }});

    // ── 4. projects: give every member a project role ────────────────────────
    db.projects.find({{}}).forEach(function (project) {{
        summary.projects.total++;
        const members = Array.isArray(project.members) ? project.members : [];
        if (members.length === 0) {{ return; }}

        const validRole = function (role) {{ return role === PROJECT_ADMIN || role === RESEARCHER; }};
        const anyRoleAssigned = members.some(function (m) {{ return m && validRole(m.role); }});

        let changed = false;
        const newMembers = members.map(function (member, index) {{
            if (!member) {{ return member; }}
            if (validRole(member.role)) {{ return member; }}

            // A project that has never been migrated: members[0] is the creator
            // (createProject wrote them as the sole initial member), so they get
            // the admin role and everyone else becomes a researcher. A partially
            // migrated project only gets its gaps filled, as researchers.
            const role = (!anyRoleAssigned && index === 0) ? PROJECT_ADMIN : RESEARCHER;
            changed = true;
            summary.projects.membersAssigned++;
            return Object.assign({{}}, member, {{ role: role }});
        }});

        // Never leave a project nobody can administer.
        const hasAdmin = newMembers.some(function (m) {{ return m && m.role === PROJECT_ADMIN; }});
        if (!hasAdmin) {{
            newMembers[0].role = PROJECT_ADMIN;
            summary.projects.adminsPromoted++;
            changed = true;
        }}

        if (changed) {{
            summary.projects.changed++;
            if (APPLY) {{
                db.projects.updateOne({{ _id: project._id }}, {{ $set: {{ members: newMembers }} }});
            }}
        }}
    }});

    // ── 5. invite codes: projectIds[] -> projectId, role, eppn ───────────────
    db.invite_codes.find({{}}).forEach(function (code) {{
        summary.inviteCodes.total++;

        const set = {{}};
        const unset = {{}};
        let changed = false;

        // projectIds was an array but the UI only ever assigned one project.
        if (typeof code.projectId === "undefined") {{
            const ids = Array.isArray(code.projectIds) ? code.projectIds.filter(Boolean) : [];
            set.projectId = ids.length > 0 ? ids[0] : null;
            if (ids.length > 1) {{
                summary.warnings.push(
                    "Invite code " + code.code + " listed " + ids.length +
                    " projects; kept " + ids[0]);
            }}
            summary.inviteCodes.projectIdMigrated++;
            changed = true;
        }}
        if (typeof code.projectIds !== "undefined") {{ unset.projectIds = ""; changed = true; }}

        const mappedRole = INVITE_ROLE_MAP[code.role] || RESEARCHER;
        if (code.role !== mappedRole) {{
            set.role = mappedRole;
            summary.inviteCodes.roleRemapped++;
            changed = true;
        }}

        if (typeof code.eppn === "undefined") {{
            set.eppn = null;
            summary.inviteCodes.eppnInitialised++;
            changed = true;
        }}

        const effectiveProjectId =
            typeof set.projectId !== "undefined" ? set.projectId : code.projectId;
        const isOrphan = !effectiveProjectId && code.used !== true;
        if (isOrphan) {{
            summary.inviteCodes.orphaned++;
            if (DELETE_ORPHAN_CODES) {{
                if (APPLY) {{ db.invite_codes.deleteOne({{ _id: code._id }}); }}
                summary.inviteCodes.orphansDeleted++;
                return;
            }}
        }}

        if (changed) {{
            summary.inviteCodes.changed++;
            if (APPLY) {{
                const update = {{}};
                if (Object.keys(set).length > 0) {{ update.$set = set; }}
                if (Object.keys(unset).length > 0) {{ update.$unset = unset; }}
                db.invite_codes.updateOne({{ _id: code._id }}, update);
            }}
        }}
    }});

    // ── 6. sanity check ──────────────────────────────────────────────────────
    const sysAdminCount = APPLY
        ? db.users.countDocuments({{ system_role: SYS_ADMIN }})
        : db.users.countDocuments({{
            $or: [
                {{ system_role: SYS_ADMIN }},
                {{ role: SYS_ADMIN }},
                {{ role: "sysadmin" }},
                {{ "privileges.sysAdmin": true }},
            ],
        }});
    summary.sysAdminCount = sysAdminCount;
    if (sysAdminCount === 0) {{
        summary.warnings.push(
            "No user has the sys_admin system role. Nobody can reach the admin " +
            "panel or create projects — grant it with: " +
            "./visp.py users set-system-role <username> sys_admin");
    }}

    return summary;
}})()
"""


def print_report(summary: dict, apply_changes: bool) -> None:
    verb = "Applied" if apply_changes else "Would apply"
    header = "MIGRATION APPLIED" if apply_changes else "DRY RUN — no changes written"
    print(color(f"=== {header} ===", C.BOLD + C.CYAN))
    print()

    collections = summary.get("collections", {})
    print(color("Collections", C.CYAN))
    if collections.get("renamedRolesToSystemRoles"):
        print(f"  {verb}: rename 'roles' -> 'system_roles'")
    elif collections.get("droppedLegacyRoles"):
        print(f"  {verb}: drop leftover legacy 'roles' ('system_roles' already present)")
    else:
        print("  no legacy 'roles' collection found (nothing to rename)")

    system_roles = summary.get("systemRoles", {})
    project_roles = summary.get("projectRoles", {})
    print(
        f"  system_roles:  {system_roles.get('seeded', 0)} seeded/updated, "
        f"{system_roles.get('removed', 0)} stale removed"
    )
    print(
        f"  project_roles: {project_roles.get('seeded', 0)} seeded/updated, "
        f"{project_roles.get('removed', 0)} stale removed"
    )
    print()

    users = summary.get("users", {})
    print(color("Users", C.CYAN))
    print(f"  total:                 {users.get('total', 0)}")
    print(f"  -> sys_admin:          {users.get('toSysAdmin', 0)}")
    print(f"  -> user:               {users.get('toUser', 0)}")
    print(f"  already correct:       {users.get('alreadyCorrect', 0)}")
    print(f"  legacy fields cleared: {users.get('legacyFieldsCleared', 0)}  (privileges, role)")
    print()

    projects = summary.get("projects", {})
    print(color("Projects", C.CYAN))
    print(f"  total:              {projects.get('total', 0)}")
    print(f"  changed:            {projects.get('changed', 0)}")
    print(f"  members given role: {projects.get('membersAssigned', 0)}")
    print(f"  admins promoted:    {projects.get('adminsPromoted', 0)}  (projects that had none)")
    print()

    codes = summary.get("inviteCodes", {})
    print(color("Invite codes", C.CYAN))
    print(f"  total:              {codes.get('total', 0)}")
    print(f"  changed:            {codes.get('changed', 0)}")
    print(f"  projectIds->projectId: {codes.get('projectIdMigrated', 0)}")
    print(f"  role remapped:      {codes.get('roleRemapped', 0)}")
    print(f"  eppn initialised:   {codes.get('eppnInitialised', 0)}")
    orphaned = codes.get("orphaned", 0)
    if orphaned:
        deleted = codes.get("orphansDeleted", 0)
        note = f"{deleted} deleted" if deleted else "kept — re-run with --delete-orphan-invite-codes to remove"
        print(color(f"  unused, no project: {orphaned}  ({note})", C.YELLOW))
    print()

    print(color("System admins after migration: ", C.CYAN) + str(summary.get("sysAdminCount", 0)))

    warnings = summary.get("warnings", [])
    if warnings:
        print()
        print(color("Warnings", C.YELLOW))
        for warning in warnings:
            print(color(f"  ! {warning}", C.YELLOW))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate a VISP database to the two-tier system/project role model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the changes. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--delete-orphan-invite-codes",
        action="store_true",
        help="Delete unused invite codes that have no project (they cannot be redeemed into one).",
    )
    args = parser.parse_args()

    if not args.apply:
        print(color("Running in DRY RUN mode — pass --apply to write changes.", C.YELLOW))
        print()

    summary = mongosh_json(build_migration_js(args.apply, args.delete_orphan_invite_codes))

    if not isinstance(summary, dict):
        print(color("Migration failed: unexpected response from mongosh", C.RED), file=sys.stderr)
        print(repr(summary), file=sys.stderr)
        return 1

    print_report(summary, args.apply)

    if args.apply:
        print()
        print(color("Done. Restart session-manager so it picks up the new collections:", C.GREEN))
        print("  ./visp.py restart session-manager")
    else:
        print()
        print(color("Re-run with --apply to perform the migration.", C.CYAN))

    return 0


if __name__ == "__main__":
    sys.exit(main())
