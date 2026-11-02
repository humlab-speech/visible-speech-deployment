// Dev only: pre-authorize the local IdP's test accounts so a fresh dev install
// can be used straight away, without an invite code or a trip to vispctl.
// Mounted into /docker-entrypoint-initdb.d by quadlets/dev/mongo.container, so
// mongo runs it once, when it initializes an empty data directory. The prod
// quadlet never mounts it.
//
// The accounts mirror mounts/local-idp/config/authsources.php; username is the
// eppn as index.php's formatEppn() writes it. On first sign-in index.php finds
// the document and fills in the session/login fields itself.
//
//   test1 - sys_admin (can create projects and reach /admin)
//   test2 - plain user
//   test3 - deliberately left out, to exercise the invite-code flow
const visp = db.getSiblingDB("visp");

const users = [
    { n: 1, name: "One", system_role: "sys_admin" },
    { n: 2, name: "Two", system_role: "user" },
];

visp.users.insertMany(
    users.map((u) => ({
        firstName: "Test",
        lastName: `User ${u.name}`,
        fullName: `Test User ${u.name}`,
        email: `testuser${u.n}@visp.local`,
        eppn: `testuser${u.n}@visp.local`,
        username: `testuser${u.n}_at_visp_dot_local`,
        loginAllowed: true,
        system_role: u.system_role,
    })),
);
