#!/usr/bin/env node
'use strict';

/**
 * Sync/review script: scans disk repositories for VISP_emuDB sessions/bundles/files
 * and outputs a JSON structure suitable for reviewing before any DB updates.
 *
 * By default: NO database writes. It only prints JSON.
 *
 * Usage:
 *   node sync-visp-sessions.js [command] [options]
 *
 * Run without arguments (or with "help") to see full usage information.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function printHelp() {
  const help = `
Usage: node sync-visp-sessions.js [command] [options]

Commands:
  scan              Scan disk repositories and output JSON
  review            Scan disk + fetch Mongo docs for comparison
  test-db           Test MongoDB connection and authentication
  help              Show this help message (default when no arguments are given)

Options:
  --root <path>     Root repositories dir (default: ../mounts/repositories)
  --env <path>      Path to env file (default: ../.env)
  --out <path>      Write output JSON to a file as well as stdout
  --pretty          Pretty-print JSON (default: true)

  --project <id>    Only operate on a specific project (by project ID)

Mongo options:
  --db <name>       DB name (default: "visp")
  --collection <c>  Collection name (default: "projects")

Dangerous mode (writes):
  --apply           Apply merged data to MongoDB

Examples:
  node sync-visp-sessions.js scan
  node sync-visp-sessions.js scan --project abc123DEF456
  node sync-visp-sessions.js scan --apply --project abc123DEF456
  node sync-visp-sessions.js review
  node sync-visp-sessions.js test-db
  node sync-visp-sessions.js --root /data/repos --out result.json
`.trimStart();
  console.log(help);
}

function parseArgs(argv) {
  const args = { _commands: [] };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--pretty') args.pretty = true;
    else if (a === '--no-pretty') args.pretty = false;
    else if (a === '--apply') args.apply = true;
    else if (a === '--root') args.root = argv[++i];
    else if (a === '--env') args.env = argv[++i];
    else if (a === '--out') args.out = argv[++i];
    else if (a === '--db') args.db = argv[++i];
    else if (a === '--collection') args.collection = argv[++i];
    else if (a === '--project') args.project = argv[++i];
    else if (a === '--help' || a === '-h') args._commands.push('help');
    else if (!a.startsWith('-')) args._commands.push(a);
    else {
      // ignore unknown flags to stay robust
    }
  }
  return args;
}

function readEnvVarFromFile(envPath, varName) {
  const content = fs.readFileSync(envPath, 'utf8');
  // supports: VAR=value, VAR="value", VAR='value' (no export required)
  const re = new RegExp(`^\\s*(?:export\\s+)?${escapeRegExp(varName)}\\s*=\\s*(.*)\\s*$`, 'm');
  const m = content.match(re);
  if (!m) return null;

  let v = m[1].trim();
  // strip inline comments for unquoted values: VAR=abc # comment
  const isQuoted = (v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"));
  if (!isQuoted) {
    v = v.split(/\s+#/)[0].trim();
  }
  // strip quotes
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    v = v.slice(1, -1);
  }
  return v;
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isProjectIdDirName(name) {
  // Project IDs may contain A-Z a-z 0-9 as well as hyphens and underscores.
  // Keep it slightly strict to avoid scanning random dirs:
  // at least 10 chars.
  return /^[A-Za-z0-9_-]{10,}$/.test(name);
}

function isSessionDirName(name) {
  // example: "Session 1_ses" (in emuDB standard)
  return /_ses$/.test(name);
}

function isBundleDirName(name) {
  // example: "msajc023_bndl"
  return /_bndl$/.test(name);
}

function safeStat(p) {
  try { return fs.statSync(p); } catch { return null; }
}

function listDirs(p) {
  let ents;
  try {
    ents = fs.readdirSync(p, { withFileTypes: true });
  } catch {
    return [];
  }
  return ents.filter(e => e.isDirectory()).map(e => e.name);
}

function listFilesRecursively(dir) {
  // only files (no dirs), returns relative paths from dir
  const out = [];
  function walk(cur, relBase) {
    let ents;
    try {
      ents = fs.readdirSync(cur, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of ents) {
      const abs = path.join(cur, e.name);
      const rel = relBase ? path.join(relBase, e.name) : e.name;
      if (e.isDirectory()) walk(abs, rel);
      else if (e.isFile()) out.push({ abs, rel });
    }
  }
  walk(dir, '');
  return out;
}

function guessMimeType(filename) {
  const ext = path.extname(filename).toLowerCase();
  // minimal mapping (extend if needed)
  if (ext === '.wav') return 'audio/wav';
  if (ext === '.mp3') return 'audio/mpeg';
  if (ext === '.flac') return 'audio/flac';
  if (ext === '.json') return 'application/json';
  if (ext === '.sqlite') return 'application/x-sqlite3';
  if (ext === '.txt') return 'text/plain';
  return 'application/octet-stream';
}

function buildDiskModel(reposRoot) {
  const projects = [];

  const projectDirs = listDirs(reposRoot).filter(isProjectIdDirName);
  for (const projectId of projectDirs) {
    const projectPath = path.join(reposRoot, projectId);
    const emuDbPath = path.join(projectPath, 'Data', 'VISP_emuDB');

    const emuStat = safeStat(emuDbPath);
    if (!emuStat || !emuStat.isDirectory()) {
      projects.push({
        projectId,
        projectPath,
        warnings: [`Missing Data/VISP_emuDB directory`],
        sessions: []
      });
      continue;
    }

    const sessionDirs = listDirs(emuDbPath).filter(isSessionDirName);

    const sessions = [];
    for (const sessionDir of sessionDirs) {
      const sessionPath = path.join(emuDbPath, sessionDir);
      const sessionName = sessionDir.replace(/_ses$/, '');

      // bundles are inside session dir
      const bundleDirs = listDirs(sessionPath).filter(isBundleDirName);

      // Read session metadata from <sessionName>.json (legacy: .meta_json)
      let speakerGender = null;
      let speakerAge = null;
      const sessionMetaCandidates = [
        path.join(sessionPath, `${sessionName}.json`),
        path.join(sessionPath, `${sessionName}.meta_json`)
      ];
      for (const metaPath of sessionMetaCandidates) {
        try {
          const metaRaw = fs.readFileSync(metaPath, 'utf8');
          const meta = JSON.parse(metaRaw);
          if (meta.Gender != null) {
            const genderMap = { 'Woman': 'Female', 'Man': 'Male' };
            speakerGender = genderMap[meta.Gender] || meta.Gender;
          }
          if (meta.Age != null) {
            speakerAge = meta.Age;
          }
          break;
        } catch {
          // Try next candidate
        }
      }

      // A VISP "session" in your DB model looks like:
      // { id, name, speakerGender, speakerAge, dataSource, ... files: [] }
      // We don't know DB session id here, so we generate a stable-ish deterministic id
      // derived from projectId + sessionDir (review-only; replace with DB id during apply).
      const derivedSessionId = deterministicId(`${projectId}:${sessionDir}`);

      // Only audio files are included; other files (json, etc.) are implied.
      const audioExtensions = new Set(['.wav', '.mp3', '.flac', '.ogg', '.aac', '.m4a']);
      const files = [];

      for (const bundleDir of bundleDirs) {
        const bundlePath = path.join(sessionPath, bundleDir);

        const fileItems = listFilesRecursively(bundlePath);
        for (const f of fileItems) {
          const ext = path.extname(f.rel).toLowerCase();
          if (!audioExtensions.has(ext)) continue;
          const st = safeStat(f.abs);
          if (!st || !st.isFile()) continue;
          files.push({
            name: path.basename(f.rel),
            size: st.size,
            type: guessMimeType(f.rel)
          });
        }
      }

      sessions.push({
        id: derivedSessionId,
        name: sessionName,
        speakerGender,
        speakerAge,
        dataSource: 'upload',
        sprScriptName: null,
        sessionScript: null,
        sessionId: null,
        files
      });
    }

    projects.push({
      projectId,
      projectPath,
      warnings: [],
      sessions
    });
  }

  return { reposRoot, projects };
}

function deterministicId(input) {
  // produces URL-safe-ish short id; stable for same input.
  // Example output resembles your ids but not guaranteed same alphabet.
  // If you want strictly alphanumeric, base64url is almost-alphanumeric (has _ and -).
  // We'll convert to base62-ish by stripping non-alnum and padding.
  const h = crypto.createHash('sha1').update(input).digest('base64url');
  const alnum = h.replace(/[^A-Za-z0-9]/g, '');
  return (alnum + '00000000000000000000').slice(0, 21);
}

async function connectMongo(args, mongoRootPassword) {
  let MongoClient;
  try {
    ({ MongoClient } = require('mongodb'));
  } catch (e) {
    throw new Error(
      `"mongodb" package is not installed. Run: npm i mongodb`
    );
  }

  if (!mongoRootPassword) {
    throw new Error('MONGO_ROOT_PASSWORD not found in env file');
  }

  const uri = `mongodb://root:${encodeURIComponent(mongoRootPassword)}@localhost:27017/?authSource=admin`;

  const client = new MongoClient(uri, { serverSelectionTimeoutMS: 8000 });
  await client.connect();

  const dbName = args.db || 'visp';
  const collectionName = args.collection || 'projects';

  const db = client.db(dbName);
  const col = db.collection(collectionName);

  return { client, db, col };
}

function mergeFiles(mongoFiles, diskFiles) {
  // Index disk files by name for quick lookup
  const diskByName = new Map(diskFiles.map(f => [f.name, f]));
  const merged = [];

  // Update existing mongo files: fill in null size/type from disk
  const seen = new Set();
  for (const mf of mongoFiles) {
    const df = diskByName.get(mf.name);
    if (df) {
      seen.add(mf.name);
      merged.push({
        name: mf.name,
        size: mf.size != null ? mf.size : df.size,
        type: mf.type != null ? mf.type : df.type
      });
    } else {
      merged.push({ ...mf });
    }
  }

  // Add disk files not already present in mongo
  for (const df of diskFiles) {
    if (!seen.has(df.name)) {
      merged.push({ ...df });
    }
  }

  return merged;
}

function mergeSession(mongoSession, diskSession) {
  // Start from mongo session, only fill in null fields from disk
  const merged = { ...mongoSession };

  if (merged.speakerGender == null && diskSession.speakerGender != null) {
    merged.speakerGender = diskSession.speakerGender;
  }
  if (merged.speakerAge == null && diskSession.speakerAge != null) {
    merged.speakerAge = diskSession.speakerAge;
  }

  // Merge files: preserve existing, fill nulls, add new
  merged.files = mergeFiles(mongoSession.files || [], diskSession.files || []);

  return merged;
}

function mergeForReview(diskModel, mongoDocsById) {
  // Preserves all existing Mongo data. Fills in null values and adds new
  // sessions/files discovered on disk.
  const out = [];

  for (const p of diskModel.projects) {
    const mongoDoc = mongoDocsById ? mongoDocsById.get(p.projectId) : null;

    if (!mongoDoc) {
      out.push({
        id: p.projectId,
        _mongoFound: false,
        _diskProjectPath: p.projectPath,
        warnings: p.warnings,
        sessions: p.sessions
      });
      continue;
    }

    // Build a map of disk sessions by name
    const diskByName = new Map(p.sessions.map(s => [s.name, s]));

    const mergedSessions = (mongoDoc.sessions || []).map(ms => {
      const disk = diskByName.get(ms.name);
      if (!disk) {
        return {
          ...ms,
          _diskFound: false
        };
      }
      return {
        ...mergeSession(ms, disk),
        _diskFound: true
      };
    });

    // Also include disk sessions that don't exist in mongo (by name)
    const mongoNames = new Set((mongoDoc.sessions || []).map(s => s.name));
    for (const ds of p.sessions) {
      if (!mongoNames.has(ds.name)) {
        mergedSessions.push({
          ...ds,
          _mongoFound: false
        });
      }
    }

    out.push({
      ...mongoDoc,
      _mongoFound: true,
      _diskProjectPath: p.projectPath,
      warnings: p.warnings,
      sessions: mergedSessions
    });
  }

  return out;
}

async function main() {
  const args = parseArgs(process.argv);

  // Show help if explicitly requested or if no arguments were given at all
  const command = args._commands[0] || null;
  if (command === 'help' || process.argv.length <= 2) {
    printHelp();
    process.exit(0);
  }

  const pretty = args.pretty !== false;

  const envPath = path.resolve(__dirname, args.env || '../.env');
  const reposRoot = path.resolve(__dirname, args.root || '../mounts/repositories');

  if (!fs.existsSync(envPath)) {
    console.error(`ERROR: env file not found: ${envPath}`);
    process.exit(1);
  }

  const mongoRootPassword = readEnvVarFromFile(envPath, 'MONGO_ROOT_PASSWORD');

  // test-db: just verify we can connect and authenticate
  if (command === 'test-db') {
    let mongo;
    try {
      mongo = await connectMongo(args, mongoRootPassword);
      const dbName = args.db || 'visp';
      const result = await mongo.db.command({ ping: 1 });
      console.log(`OK: connected to MongoDB (db: ${dbName}, ping: ${JSON.stringify(result)})`);
      const collections = await mongo.db.listCollections().toArray();
      console.log(`Collections in "${dbName}": ${collections.map(c => c.name).join(', ') || '(none)'}`);
    } catch (err) {
      console.error(`FAILED: ${err.message}`);
      process.exit(1);
    } finally {
      if (mongo?.client) await mongo.client.close();
    }
    process.exit(0);
  }

  if (!fs.existsSync(reposRoot)) {
    console.error(`ERROR: repositories root not found: ${reposRoot}`);
    process.exit(1);
  }

  let diskModel = buildDiskModel(reposRoot);

  // Filter to a specific project if --project was provided
  if (args.project) {
    const before = diskModel.projects.length;
    diskModel.projects = diskModel.projects.filter(p => p.projectId === args.project);
    if (diskModel.projects.length === 0) {
      console.error(`ERROR: no project found on disk with id "${args.project}" (scanned ${before} project(s))`);
      process.exit(1);
    }
  }

  // Default output: disk-only model (no mongo)
  let review = {
    meta: {
      envPath,
      reposRoot,
      mongoRootPasswordFound: !!mongoRootPassword,
      mode: 'disk-scan-only',
      generatedAt: new Date().toISOString()
    },
    projects: diskModel.projects
  };

  // Try to connect to MongoDB and merge with existing data.
  // If connection fails (e.g. no mongo running), fall back to disk-only for 'scan'.
  let mongo = null;
  try {
    mongo = await connectMongo(args, mongoRootPassword);

    // Fetch mongo docs for any matching disk project ids
    const projectIds = diskModel.projects.map(p => p.projectId);
    const mongoDocs = await mongo.col.find({ id: { $in: projectIds } }).toArray();

    const map = new Map();
    for (const d of mongoDocs) map.set(d.id, d);

    const merged = mergeForReview(diskModel, map);

    review = {
      meta: {
        envPath,
        reposRoot,
        mongoRootPasswordFound: !!mongoRootPassword,
        mode: command === 'review' ? 'disk+mongo-review (no writes)' : 'disk+mongo-scan (no writes)',
        db: mongo.col.dbName,
        collection: mongo.col.collectionName,
        generatedAt: new Date().toISOString()
      },
      projects: merged
    };
  } catch (err) {
    if (command === 'review') {
      // review requires mongo — fail hard
      throw err;
    }
    // scan: mongo not available, proceed with disk-only
    console.error(`WARNING: could not connect to MongoDB, outputting disk-only data (${err.message})`);
  }

  // --apply mode: update the database with merged data, output progress instead of JSON
  if (args.apply) {
    if (!mongo) {
      console.error('ERROR: --apply requires a working MongoDB connection.');
      process.exit(2);
    }

    console.log(`Applying updates to MongoDB (db: ${mongo.db.databaseName}, collection: ${mongo.col.collectionName})...`);
    console.log(`Projects to process: ${review.projects.length}\n`);

    let projectsUpdated = 0;
    let projectsSkipped = 0;
    let sessionsUpdated = 0;
    let sessionsAdded = 0;
    let filesUpdated = 0;
    let filesAdded = 0;

    for (const project of review.projects) {
      if (!project._mongoFound) {
        console.log(`[SKIP] Project "${project.id}" — not found in MongoDB, skipping (create manually first)`);
        projectsSkipped++;
        continue;
      }

      console.log(`[PROJECT] ${project.id} (${project.name || 'unnamed'})`);

      // Build the sessions array to write back
      const updatedSessions = [];
      for (const session of project.sessions) {
        if (session._mongoFound === false) {
          // New session from disk, not in mongo
          const { _mongoFound, ...cleanSession } = session;
          updatedSessions.push(cleanSession);
          sessionsAdded++;
          console.log(`  [+] New session: "${session.name}" (${session.files?.length || 0} files)`);
        } else if (session._diskFound === false) {
          // Session only in mongo, no disk counterpart — keep as-is
          const { _diskFound, ...cleanSession } = session;
          updatedSessions.push(cleanSession);
          console.log(`  [=] Session "${session.name}" — no disk data, kept as-is`);
        } else {
          // Merged session
          const { _diskFound, ...cleanSession } = session;
          updatedSessions.push(cleanSession);
          sessionsUpdated++;

          // Count file changes
          const origSession = (project.sessions || []).find(s => s.name === session.name);
          if (origSession) {
            // Count how many files were added or had fields filled in
            // We'll compare with the original mongo doc
          }
          console.log(`  [~] Session "${session.name}" — merged (${session.files?.length || 0} files)`);
        }
      }

      // Perform the update: replace the sessions array
      const updateResult = await mongo.col.updateOne(
        { id: project.id },
        { $set: { sessions: updatedSessions } }
      );

      if (updateResult.modifiedCount > 0) {
        projectsUpdated++;
        console.log(`  [OK] Updated in database (matched: ${updateResult.matchedCount}, modified: ${updateResult.modifiedCount})`);
      } else {
        console.log(`  [=] No changes needed (matched: ${updateResult.matchedCount}, modified: ${updateResult.modifiedCount})`);
      }
      console.log('');
    }

    console.log('--- Summary ---');
    console.log(`Projects updated: ${projectsUpdated}`);
    console.log(`Projects skipped (not in DB): ${projectsSkipped}`);
    console.log(`Sessions merged: ${sessionsUpdated}`);
    console.log(`Sessions added: ${sessionsAdded}`);
    console.log('Done.');

  } else {
    // Normal mode: output JSON
    const json = pretty ? JSON.stringify(review, null, 2) : JSON.stringify(review);

    // stdout
    process.stdout.write(json + '\n');

    // optional output file
    if (args.out) {
      const outPath = path.resolve(process.cwd(), args.out);
      fs.writeFileSync(outPath, json, 'utf8');
      console.error(`Wrote ${outPath}`);
    }
  }

  if (mongo?.client) await mongo.client.close();
}

main().catch(err => {
  console.error(`FATAL: ${err?.stack || err}`);
  process.exit(1);
});
