// Probe: a session that holds recordings shows its script control locked, and a
// session that does not must not. Hand-run diagnostic, not part of ./visp.py test
// recording - run it as: VISP_URL=... VISP_USER=... VISP_PASSWORD=... VISP_PROJECT=...
// node tests/e2e/probe-script-lock.mjs (dev mode, from a machine that can reach the stack).
import { chromium } from "playwright";
import fs from "node:fs";

const baseUrl = process.env.VISP_URL || "https://visp.local";
const cfg = {
    username: process.env.VISP_USER || "test1",
    password: process.env.VISP_PASSWORD || "test1pass",
    project: process.env.VISP_PROJECT || "TestProject2",
};

const domClick = (l) => l.evaluate((el) => el.click());

async function login(page) {
    await page.goto(baseUrl + "/", { waitUntil: "networkidle" });
    await page.getByText("Sign in").first().click();
    await page.locator("input[name=username]").waitFor({ timeout: 30000 });
    await page.fill("input[name=username]", cfg.username);
    await page.fill("input[name=password]", cfg.password);
    await page.locator("button[type=submit], input[type=submit]").first().click();
    await page.waitForURL((u) => u.href.startsWith(baseUrl + "/"), { timeout: 30000 });
    await page.getByText("Your Projects").waitFor({ timeout: 30000 });
    console.log("[probe] logged in");
}

function makeWav(seconds = 0.5, rate = 16000) {
    const samples = Math.round(seconds * rate);
    const buf = Buffer.alloc(44 + samples * 2);
    buf.write("RIFF", 0); buf.writeUInt32LE(36 + samples * 2, 4); buf.write("WAVEfmt ", 8);
    buf.writeUInt32LE(16, 8 + 4); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
    buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(16, 32);
    buf.writeUInt16LE(1, 34); buf.write("data", 36); buf.writeUInt32LE(samples * 2, 40);
    for (let i = 0; i < samples; i++) buf.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * 440 * i) / rate)), 44 + i * 2);
    return buf;
}

const browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1400, height: 900 }, permissions: ["microphone"] });
const page = await context.newPage();
let ok = false;
try {
    await login(page);
    // Upload a file to a new session, then switch recording on: after saving,
    // the session has files but no recording-origin takes yet. Instead reuse
    // the recorded-state check directly: create an upload session with a file.
    await page.goto(baseUrl + "/", { waitUntil: "networkidle" });
    const title = page.getByText(cfg.project, { exact: true }).first();
    await title.waitFor({ timeout: 30000 });
    const card = title.locator('xpath=ancestor::*[.//text()[contains(., "Recording sessions")]][1]');
    await domClick(card.getByText("Recording sessions", { exact: true }));
    const dialog = page.locator(".project-dialog-container");
    await dialog.waitFor({ timeout: 30000 });
    await page.waitForFunction(() => {
        const add = document.querySelector("button.add-session-btn");
        return add && !add.disabled && !document.querySelector(".sessions-loading");
    }, null, { timeout: 60000 });

    // A recording-origin session from a prior e2e run may not exist; create one
    // the cheap way: a session with an uploaded file does not lock the script.
    // The lock applies to *recordings*, so use the same upload+record switch:
    // upload -> save -> enable recording with script -> save. The session now
    // holds an upload-origin file; switching recording off afterwards is what
    // the source lock guards. For the SCRIPT lock we need a recording-origin
    // file, so this probe instead verifies the negative: a session without
    // recordings must have an UNLOCKED control, and any session in the dialog
    // that lists "Imported" recordings must have the control locked.
    const cards = dialog.locator(".session-card");
    const n = await cards.count();
    console.log(`[probe] ${n} session cards; auditing each for script-lock consistency`);
    let problems = [];
    let audited = 0;
    for (let i = 0; i < n; i++) {
        const c = cards.nth(i);
        await domClick(c.locator(".session-card-header"));
        await page.waitForTimeout(400);
        const text = await c.innerText();
        // Two panel states mean "this session has takes": imported, and queued for
        // import (sessions-form's getSprImportStatus reports pending/importing as one
        // "busy" label and the lock covers both). During the queued window the prompt
        // files are not listed yet, so only the imported reading checks for them.
        const hasRecordings =
            (/Imported/.test(text) && /prompt_\d/.test(text)) ||
            /Importing new recordings/.test(text);
        const sel = c.locator("select.sessionScriptControl").first();
        if ((await sel.count()) === 0) continue;
        audited++;
        const st = await sel.evaluate((el) => ({
            locked: el.hasAttribute("disabled"),
            stored: el.value,
            offered: [...el.options].map((o) => o.value).filter(Boolean),
        }));
        // Since f75fc23 the lock is conditional: a session with recordings stays
        // unlocked when the script it would hold is no longer among the options
        // (deleted script, or a session predating the link) — the escape hatch.
        const expectLocked = hasRecordings && !!st.stored && st.offered.includes(st.stored);
        const name = (await c.locator(".session-title").first().innerText()).trim();
        console.log(`[probe] "${name}": recordings=${hasRecordings} scriptLocked=${st.locked} expectLocked=${expectLocked}`);
        if (expectLocked && !st.locked) problems.push(`"${name}" holds an offered recording script but the control is not locked`);
        if (!expectLocked && st.locked) problems.push(`"${name}" must not be locked: ${hasRecordings ? "its stored script is no longer offered" : "it has no recordings"}`);
    }
    ok = problems.length === 0 && audited > 0;
    if (audited === 0) {
        // An audit that looked at nothing has no verdict to report, and used to print
        // the success line anyway - on a project with no cards, or with the selects
        // missing because the dialog never finished loading.
        problems.push(`audited ${n} session cards and compared ${audited} script controls`);
    }
    if (!ok) console.error("[probe] PROBLEMS:\n" + problems.join("\n"));
    else console.log("[probe] ✓ script-control locking is consistent with recording state");
} catch (err) {
    console.error("[probe] FAILED:", err.message);
} finally {
    await browser.close();
    process.exit(ok ? 0 : 1);
}
