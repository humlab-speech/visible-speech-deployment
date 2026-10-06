// End-to-end test of the online recording pipeline:
//
//   log in → create an online-recording session → record every prompt in the
//   web recorder → confirm the recordings are imported into the project.
//
// It also covers the failure modes the pipeline has had:
//   - re-recording a prompt: the final take, not an earlier one, is imported
//   - reloading the recorder mid-session: earlier prompts still count as done,
//     so the session can be completed without re-recording them
//
// A session can take both uploads and online recordings: it then uploads a
// file to the recorded session and checks that the recordings are untouched,
// after which recording can't be switched off and the recording script can't
// be changed.
// It also uploads a file to a new session and checks that it is listed under
// the name it was stored as, and that its bundle holds the uploaded audio.
//
// Runs in the Playwright container via `./visp.py test recording` (dev mode only:
// it logs in through the local IdP). Chromium's fake microphone supplies the
// audio. Configuration comes from the environment; see readConfig().

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

function readConfig() {
    const baseUrl = process.env.VISP_URL || "https://visp.local";
    return {
        baseUrl,
        username: process.env.VISP_USER || "test1",
        password: process.env.VISP_PASSWORD || "test1pass",
        project: process.env.VISP_PROJECT || "Test 3",
        keep: process.env.VISP_KEEP === "1",
        importTimeoutMs: Number(process.env.VISP_IMPORT_TIMEOUT_MS || 180000),
        repositoriesDir: process.env.VISP_REPOSITORIES_DIR || "",
        artifactsDir: process.env.VISP_ARTIFACTS_DIR || "artifacts",
    };
}

const cfg = readConfig();
let step = "starting";

function log(msg) {
    console.log(`[recording] ${msg}`);
}

function pass(msg) {
    console.log(`[recording] ✓ ${msg}`);
}

class TestFailure extends Error {}

function fail(msg) {
    throw new TestFailure(msg);
}

// ---------------------------------------------------------------- dashboard

async function login(page) {
    step = "log in";
    await page.goto(cfg.baseUrl + "/", { waitUntil: "networkidle" });
    await page.getByText("Sign in").first().click();
    await page.locator("input[name=username]").waitFor({ timeout: 30000 });
    await page.fill("input[name=username]", cfg.username);
    await page.fill("input[name=password]", cfg.password);
    await page.locator("button[type=submit], input[type=submit]").first().click();
    await page.waitForURL((url) => url.href.startsWith(cfg.baseUrl + "/"), { timeout: 30000 });
    await page.getByText("Your Projects").waitFor({ timeout: 30000 });
    pass(`logged in as ${cfg.username}`);
}

// The dashboard's fixed header and footer can cover the tiles in a headless
// viewport, so tiles and dialog buttons are clicked through the DOM.
const domClick = (locator) => locator.evaluate((el) => el.click());

function projectDialog(page) {
    return page.locator(".project-dialog-container");
}

async function openProjectDialog(page) {
    await page.goto(cfg.baseUrl + "/", { waitUntil: "networkidle" });
    const title = page.getByText(cfg.project, { exact: true }).first();
    // Projects arrive over the WebSocket after the page has loaded.
    try {
        await title.waitFor({ timeout: 30000 });
    } catch {
        fail(`project "${cfg.project}" not found on the dashboard`);
    }
    const card = title.locator('xpath=ancestor::*[.//text()[contains(., "Recording sessions")]][1]');
    await domClick(card.getByText("Recording sessions", { exact: true }));
    const dialog = projectDialog(page);
    await dialog.waitFor({ timeout: 30000 });
    // Existing sessions load asynchronously, behind a loading note.
    await page.waitForFunction(
        () => {
            const add = document.querySelector("button.add-session-btn");
            return add && !add.disabled && !document.querySelector(".sessions-loading");
        },
        null,
        { timeout: 60000 },
    );
    return dialog;
}

// The card of an existing session, matched on its exact name.
function sessionCard(page, dialog, sessionName) {
    return dialog.locator(".session-card").filter({
        has: page.locator(".session-title").getByText(sessionName, { exact: true }),
    });
}

async function saveProjectDialog(page, dialog) {
    await domClick(dialog.getByRole("button", { name: "Save" }));
    try {
        await dialog.waitFor({ state: "detached", timeout: 90000 });
    } catch {
        const errors = await dialog.locator(".form-error-msg").allInnerTexts();
        fail("project dialog did not close after Save" + (errors.length ? ": " + errors.join("; ") : ""));
    }
}

async function createRecordingSession(page, sessionName) {
    step = "create recording session";
    const dialog = await openProjectDialog(page);
    await domClick(dialog.locator("button.add-session-btn"));
    // A new session is inserted first and starts expanded.
    const nameInput = dialog.locator(".session-name-input").first();
    await nameInput.waitFor();
    await nameInput.fill(sessionName);
    // The name control only updates on blur.
    await nameInput.blur();
    // A new session has "Upload files" on; switch on "Record online" too.
    await domClick(dialog.locator("input.source-switch-input[data-source=record]").first());

    const script = dialog.locator("select.sessionScriptControl").first();
    await script.waitFor({ timeout: 15000 });
    const scriptOptions = await script.locator("option").evaluateAll((opts) =>
        opts.map((o) => o.value).filter((v) => v),
    );
    if (scriptOptions.length === 0) {
        fail(`project "${cfg.project}" has no recording scripts`);
    }
    await script.selectOption(scriptOptions[0]);
    const link = await dialog.locator("input.recordingLinkControl").first().inputValue();
    if (!link.includes("/spr/session/")) {
        fail(`unexpected recording link "${link}"`);
    }
    await saveProjectDialog(page, dialog);
    pass(`created recording session "${sessionName}"`);
    return link;
}

// Opens the project dialog and returns the expanded panel text of a session.
async function readSessionPanel(page, sessionName) {
    const dialog = await openProjectDialog(page);
    const card = sessionCard(page, dialog, sessionName);
    if ((await card.count()) !== 1) {
        return { dialog, text: null };
    }
    await domClick(card.locator(".session-card-header"));
    await page.waitForTimeout(1000);
    return { dialog, text: await card.innerText() };
}

async function waitForImport(page, sessionName, expectedFiles) {
    step = "wait for import";
    const deadline = Date.now() + cfg.importTimeoutMs;
    let lastText = "";
    while (Date.now() < deadline) {
        // The dashboard needs session-manager, which may be restarting; keep
        // polling rather than failing on the first unavailable read.
        try {
            const { text } = await readSessionPanel(page, sessionName);
            lastText = text || "";
        } catch (err) {
            lastText = `(dashboard unavailable: ${err.message})`;
            await page.waitForTimeout(5000);
            continue;
        }
        const filesListed = expectedFiles.every((f) => lastText.includes(f));
        if (lastText.includes("Import failed")) {
            fail("the UI reports the import failed:\n" + lastText);
        }
        if (filesListed && lastText.includes("Imported")) {
            pass(`import shown in the UI: ${expectedFiles.join(", ")}`);
            return;
        }
        await page.waitForTimeout(5000);
    }
    fail(`recordings were not imported within ${cfg.importTimeoutMs / 1000}s. Session panel:\n${lastText}`);
}

async function deleteRecordingSession(page, sessionName) {
    step = "clean up";
    const dialog = await openProjectDialog(page);
    // Exactly this session's card; never fall back to a broader match, or the
    // first delete button in the dialog belongs to some other session.
    const card = sessionCard(page, dialog, sessionName);
    if ((await card.count()) !== 1) {
        log(`could not identify the card of "${sessionName}"; leaving it in place`);
        return;
    }
    page.once("dialog", (d) => d.accept());
    await domClick(card.locator(".session-card-footer button.text-btn-danger"));
    await saveProjectDialog(page, dialog);
    pass(`deleted session "${sessionName}"`);
}

// Sessions this test creates are named "Smoke <stamp>" / "Smoke upload <stamp>".
const LEFTOVER_SESSION = /^Smoke (upload )?\d{12}$/;

// A run that crashed before its cleanup leaves its sessions behind; remove
// them first, all in one save. Only exact matches of the names above.
async function removeLeftoverSessions(page) {
    step = "remove leftover sessions";
    const dialog = await openProjectDialog(page);
    const titles = await dialog.locator(".session-card .session-title").allInnerTexts();
    const leftovers = titles.map((t) => t.trim()).filter((t) => LEFTOVER_SESSION.test(t));
    if (leftovers.length === 0) {
        await domClick(dialog.locator(".cancel-btn"));
        await dialog.waitFor({ state: "detached", timeout: 30000 });
        return;
    }
    const accept = (d) => d.accept();
    page.on("dialog", accept);
    try {
        for (const name of leftovers) {
            await domClick(sessionCard(page, dialog, name).locator(".session-card-footer button.text-btn-danger"));
        }
    } finally {
        page.off("dialog", accept);
    }
    await saveProjectDialog(page, dialog);
    pass(`removed ${leftovers.length} session(s) left by earlier runs: ${leftovers.join(", ")}`);
}

// ----------------------------------------------------------------- recorder

// The recorder's prompt list: one row per prompt, the current one marked
// "selRow", recorded ones with a "done" icon in the status column.
async function recorderPrompts(page) {
    return page.locator("tr:has(td.promptDescriptor)").evaluateAll((rows) =>
        rows.map((row) => ({
            index: Number(row.cells[0].innerText.trim()),
            text: row.cells[1].innerText.trim(),
            done: row.cells[2].innerText.includes("done"),
            current: row.classList.contains("selRow"),
        })),
    );
}

async function currentPromptIndex(page) {
    return (await recorderPrompts(page)).find((p) => p.current)?.index ?? null;
}

async function transportReady(page, label) {
    await page.waitForFunction(
        (l) =>
            [...document.querySelectorAll("button.transport-button-icon")].some(
                (b) => b.innerText.includes(l) && !b.disabled,
            ),
        label,
        { timeout: 30000 },
    );
}

async function clickTransport(page, label) {
    await transportReady(page, label);
    await domClick(page.locator("button.transport-button-icon", { hasText: label }).first());
}

async function openRecorder(page, link) {
    await page.goto(link, { waitUntil: "networkidle" });
    await page.getByText(/Ready\.|Recorded\./).first().waitFor({ timeout: 30000 });
    const prompts = await recorderPrompts(page);
    if (prompts.length === 0) {
        fail("recorder shows no prompts");
    }
    return prompts;
}

// Record the prompt currently shown, for about durationMs, and wait until its
// upload has been accepted. Returns the prompt's item code.
async function recordTake(page, durationMs) {
    const upload = page.waitForResponse(
        (r) => r.request().method() === "POST" && /\/recfile\//.test(r.url()),
        { timeout: 30000 },
    );
    await clickTransport(page, "Start");
    await transportReady(page, "Stop");
    await page.waitForTimeout(durationMs);
    await clickTransport(page, "Stop");
    const response = await upload;
    if (!response.ok()) {
        fail(`upload of a take failed with HTTP ${response.status()}`);
    }
    return decodeURIComponent(response.url().split("/").pop());
}

async function goToPrompt(page, index, prompts) {
    for (let i = 0; i < prompts.length + 1; i++) {
        if ((await currentPromptIndex(page)) === prompts[index].index) {
            return;
        }
        await clickTransport(page, "chevron_right");
        await page.waitForTimeout(500);
    }
    fail(`could not navigate to prompt ${index} ("${prompts[index].text}")`);
}

async function recordSession(page, link) {
    step = "record";
    let prompts = await openRecorder(page, link);
    log(`recording ${prompts.length} prompt(s): ${prompts.map((p) => p.text).join(" / ")}`);

    const itemCodes = [];
    // Two takes of the first prompt; the second, longer one must win.
    await goToPrompt(page, 0, prompts);
    await recordTake(page, 1500);
    itemCodes.push(await recordTake(page, 3000));
    pass("recorded two takes of the first prompt");

    // A reload must remember that the first prompt is done.
    step = "reload recorder";
    prompts = await openRecorder(page, link);
    if (!prompts[0].done) {
        fail("after a reload the recorder no longer shows the first prompt as recorded");
    }
    pass("recorder remembers earlier recordings after a reload");

    step = "record";
    for (let i = 1; i < prompts.length; i++) {
        await goToPrompt(page, i, prompts);
        itemCodes.push(await recordTake(page, 2000));
    }
    await page.getByText(/Session complete|Session finished/).first().waitFor({ timeout: 30000 });
    pass("recording session completed");
    return itemCodes;
}

// --------------------------------------------------------------- filesystem

// Compare every upload with its EMU-DB bundle, byte for byte. Needs the
// repositories directory mounted (./visp.py test recording does this read-only).
async function verifyBundlesOnDisk(page, link, sessionName, itemCodes) {
    step = "verify EMU-DB";
    if (!cfg.repositoriesDir) {
        log("VISP_REPOSITORIES_DIR not set; skipping the on-disk check");
        return;
    }
    const sessionId = link.split("/").pop();
    const res = await page.request.get(`${cfg.baseUrl}/spr/api/v1/session/${sessionId}`);
    const projectId = (await res.json()).project;
    const dataDir = path.join(cfg.repositoriesDir, projectId, "Data");
    for (const item of itemCodes) {
        const upload = path.join(dataDir, "speech_recorder_uploads", "emudb-sessions", sessionId, item + ".wav");
        const bundle = path.join(dataDir, "VISP_emuDB", sessionName + "_ses", item + "_bndl", item + ".wav");
        if (!fs.existsSync(bundle)) {
            fail(`bundle ${bundle} is missing`);
        }
        if (!fs.readFileSync(upload).equals(fs.readFileSync(bundle))) {
            fail(`bundle for ${item} does not hold the latest take`);
        }
    }
    pass(`EMU-DB bundles match the latest takes (${itemCodes.join(", ")})`);
}

// ------------------------------------------------------------------ uploads

// A short 16-bit mono WAV (a 440 Hz tone), so no audio fixture is needed.
function makeWav(seconds = 0.5, rate = 16000) {
    const samples = Math.round(seconds * rate);
    const buf = Buffer.alloc(44 + samples * 2);
    buf.write("RIFF", 0);
    buf.writeUInt32LE(36 + samples * 2, 4);
    buf.write("WAVEfmt ", 8);
    buf.writeUInt32LE(16, 16);
    buf.writeUInt16LE(1, 20);
    buf.writeUInt16LE(1, 22);
    buf.writeUInt32LE(rate, 24);
    buf.writeUInt32LE(rate * 2, 28);
    buf.writeUInt16LE(2, 32);
    buf.writeUInt16LE(16, 34);
    buf.write("data", 36);
    buf.writeUInt32LE(samples * 2, 40);
    for (let i = 0; i < samples; i++) {
        buf.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * 440 * i) / rate)), 44 + i * 2);
    }
    return buf;
}

// The upload handler strips characters such as "(" and turns whitespace into
// "_", so this file is stored, imported and listed as UPLOAD_STORED_NAME.
const UPLOAD_NAME = "smoke upload (1).wav";
const UPLOAD_STORED_NAME = "smoke_upload_1.wav";
const REMOVED_UPLOAD_NAME = "removed_before_saving.wav";

async function createUploadSession(page, sessionName, wav) {
    step = "create upload session";
    const dialog = await openProjectDialog(page);
    await domClick(dialog.locator("button.add-session-btn"));
    const nameInput = dialog.locator(".session-name-input").first();
    await nameInput.waitFor();
    await nameInput.fill(sessionName);
    await nameInput.blur();
    // "Upload files" is the default source of a new session. Drop a second
    // file too, and remove it again before saving: it must not be imported.
    const dropzoneInput = dialog.locator("ngx-dropzone input[type=file]").first();
    await dropzoneInput.setInputFiles({ name: UPLOAD_NAME, mimeType: "audio/wav", buffer: wav });
    await dropzoneInput.setInputFiles({ name: REMOVED_UPLOAD_NAME, mimeType: "audio/wav", buffer: wav });
    await waitForUploads(page);
    const removedPreview = dialog.locator("ngx-dropzone-preview").filter({ hasText: REMOVED_UPLOAD_NAME });
    await domClick(removedPreview.locator("ngx-dropzone-remove-badge"));
    // Removing deletes the upload on the server; Save waits for that as well.
    await waitForUploads(page);
    await saveProjectDialog(page, dialog);
    pass(`created upload session "${sessionName}" with "${UPLOAD_NAME}" (and removed "${REMOVED_UPLOAD_NAME}" before saving)`);
}

async function waitForUploads(page) {
    await page.waitForFunction(
        () => {
            const status = document.querySelector(".save-status");
            return status && !status.innerText.includes("Uploading");
        },
        null,
        { timeout: 60000 },
    );
}

// The dialog must list the file under the name it was stored as, and its
// bundle must hold exactly the uploaded audio.
async function verifyUploadSession(page, sessionName, wav) {
    step = "verify upload session";
    const { text } = await readSessionPanel(page, sessionName);
    if (!text) {
        fail(`upload session "${sessionName}" is missing from the dialog`);
    }
    if (!text.includes(UPLOAD_STORED_NAME)) {
        fail(`expected "${UPLOAD_STORED_NAME}" in the session's file list. Session panel:\n${text}`);
    }
    pass(`file listed as stored: ${UPLOAD_STORED_NAME}`);
    if (text.includes(REMOVED_UPLOAD_NAME)) {
        fail(`"${REMOVED_UPLOAD_NAME}" was removed before saving but is in the session`);
    }

    if (!cfg.repositoriesDir) {
        log("VISP_REPOSITORIES_DIR not set; skipping the on-disk check");
        return;
    }
    const bundleBase = UPLOAD_STORED_NAME.replace(/\.wav$/, "");
    const bundles = fs
        .readdirSync(cfg.repositoriesDir)
        .map((projectId) =>
            path.join(cfg.repositoriesDir, projectId, "Data", "VISP_emuDB", sessionName + "_ses",
                bundleBase + "_bndl", UPLOAD_STORED_NAME),
        )
        .filter((f) => fs.existsSync(f));
    if (bundles.length !== 1) {
        fail(`expected one bundle for ${UPLOAD_STORED_NAME}, found ${bundles.length}`);
    }
    if (!fs.readFileSync(bundles[0]).equals(wav)) {
        fail(`bundle ${bundles[0]} does not hold the uploaded audio`);
    }
    const removedBundle = path.join(path.dirname(path.dirname(bundles[0])), REMOVED_UPLOAD_NAME.replace(/\.wav$/, "_bndl"));
    if (fs.existsSync(removedBundle)) {
        fail(`"${REMOVED_UPLOAD_NAME}" was removed before saving but was imported`);
    }
    pass("EMU-DB bundle matches the uploaded file");
}

// A session can take uploads and online recordings. Uploading a file to the
// recorded session must add it without disturbing the recordings, after which
// recording can't be switched off.
async function uploadToRecordedSession(page, sessionName, wav) {
    step = "upload to recorded session";
    const { dialog } = await readSessionPanel(page, sessionName);
    const card = sessionCard(page, dialog, sessionName);
    await card
        .locator("ngx-dropzone input[type=file]")
        .setInputFiles({ name: UPLOAD_NAME, mimeType: "audio/wav", buffer: wav });
    await page.waitForFunction(
        () => !document.querySelector(".save-status")?.innerText.includes("Uploading"),
        null,
        { timeout: 60000 },
    );
    await saveProjectDialog(page, dialog);
    pass(`uploaded "${UPLOAD_NAME}" to recorded session "${sessionName}"`);
}

async function verifyRecordingLocked(page, sessionName) {
    step = "verify recording switch locked";
    const { dialog } = await readSessionPanel(page, sessionName);
    const recordSwitch = sessionCard(page, dialog, sessionName).locator("input.source-switch-input[data-source=record]");
    if (!(await recordSwitch.isChecked()) || !(await recordSwitch.isDisabled())) {
        fail("recording should be on and locked in a session with recordings");
    }
    pass("recording can't be switched off while the session has recordings");
    await domClick(dialog.locator(".cancel-btn"));
    await dialog.waitFor({ state: "detached", timeout: 30000 });
}

// Once a session holds recorded takes its recording script is locked: the
// dialog disables the control (session-manager refuses the change outright),
// and the control only unlocks as an escape hatch when the stored script is
// no longer offered — which is not the case for a session this test itself
// created minutes earlier.
async function verifyScriptChangeRefused(page, sessionName) {
    step = "verify script change refused";
    const { dialog } = await readSessionPanel(page, sessionName);
    const script = sessionCard(page, dialog, sessionName).locator("select.sessionScriptControl").first();
    await script.waitFor({ timeout: 15000 });
    const { stored, options } = await script.evaluate((el) => ({
        stored: el.value,
        options: [...el.options].map((o) => o.value).filter((v) => v),
    }));
    if (!options.includes(stored)) {
        fail(`the session's script "${stored}" is no longer offered; the lock does not apply and this test cannot check it`);
    }
    if (!(await script.isDisabled())) {
        fail("a session with recorded takes must not allow changing its recording script");
    }
    // The refusal is the disabled control; attempting the change anyway must
    // not take. With a single script offered there is nothing to switch to.
    const other = options.find((o) => o !== stored);
    if (other) {
        try {
            await script.selectOption(other, { timeout: 2000 });
            if ((await script.inputValue()) !== stored) {
                fail(`the recording script was changed to "${other}" despite the session holding takes`);
            }
        } catch {
            // selectOption refuses to act on the disabled control — that is the refusal.
        }
    }
    pass("a session with recorded takes can't change its recording script");
    await domClick(dialog.locator(".cancel-btn"));
    await dialog.waitFor({ state: "detached", timeout: 30000 });
}

// Recording can be switched on for a session created for uploads; saving
// must create the session the recording link opens.
async function enableRecordingOnUploadSession(page, sessionName) {
    step = "enable recording on upload session";
    const { dialog } = await readSessionPanel(page, sessionName);
    const card = sessionCard(page, dialog, sessionName);
    await domClick(card.locator("input.source-switch-input[data-source=record]"));
    const script = card.locator("select.sessionScriptControl");
    await script.waitFor({ timeout: 15000 });
    const scriptOptions = await script.locator("option").evaluateAll((opts) =>
        opts.map((o) => o.value).filter((v) => v),
    );
    await script.selectOption(scriptOptions[0]);
    const link = await card.locator("input.recordingLinkControl").inputValue();
    await saveProjectDialog(page, dialog);
    const sessionId = link.split("/").pop();
    const res = await page.request.get(`${cfg.baseUrl}/spr/api/v1/session/${sessionId}`);
    if (!res.ok()) {
        fail(`the recording link's session was not created (HTTP ${res.status()})`);
    }
    pass("switching on recording for an upload session creates its recording link");
}

// Uploading a file whose name is already taken in the session must be refused
// before anything is written, with the reason shown to the user.
async function verifyClashingUploadRefused(page, sessionName, wav) {
    step = "refuse clashing upload";
    const { dialog } = await readSessionPanel(page, sessionName);
    const card = sessionCard(page, dialog, sessionName);
    await card
        .locator("ngx-dropzone input[type=file]")
        .setInputFiles({ name: UPLOAD_NAME, mimeType: "audio/wav", buffer: wav });
    await page.waitForFunction(
        () => !document.querySelector(".save-status")?.innerText.includes("Uploading"),
        null,
        { timeout: 60000 },
    );
    await domClick(dialog.getByRole("button", { name: "Save" }));
    try {
        await page.getByText(/Can't save, please rename or remove these files/).first().waitFor({ timeout: 60000 });
    } catch {
        fail("saving a clashing upload was not refused");
    }
    pass("an upload with a name already in the session is refused");
    // Closing may or may not ask to discard changes; accept only while closing.
    const accept = (d) => d.accept();
    page.on("dialog", accept);
    try {
        await domClick(dialog.locator(".cancel-btn"));
        await dialog.waitFor({ state: "detached", timeout: 30000 });
    } finally {
        page.off("dialog", accept);
    }
}

// --------------------------------------------------------------------- main

async function main() {
    fs.mkdirSync(cfg.artifactsDir, { recursive: true });
    const browser = await chromium.launch({
        args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
    });
    const context = await browser.newContext({
        ignoreHTTPSErrors: true,
        viewport: { width: 1400, height: 900 },
        permissions: ["microphone"],
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => log(`page error: ${e.message}`));

    // Session names allow letters, digits, spaces, "-" and "_", max 30 chars.
    const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
    const sessionName = `Smoke ${stamp}`;
    const uploadSessionName = `Smoke upload ${stamp}`;
    let created = false;
    let uploadCreated = false;
    try {
        await login(page);
        await removeLeftoverSessions(page);
        const link = await createRecordingSession(page, sessionName);
        created = true;
        const itemCodes = await recordSession(page, link);
        await waitForImport(page, sessionName, itemCodes.map((c) => c + ".wav"));
        await verifyBundlesOnDisk(page, link, sessionName, itemCodes);

        const wav = makeWav();
        await uploadToRecordedSession(page, sessionName, wav);
        await verifyUploadSession(page, sessionName, wav);
        await verifyBundlesOnDisk(page, link, sessionName, itemCodes);
        await verifyRecordingLocked(page, sessionName);
        await verifyScriptChangeRefused(page, sessionName);

        await createUploadSession(page, uploadSessionName, wav);
        uploadCreated = true;
        await verifyUploadSession(page, uploadSessionName, wav);
        await verifyClashingUploadRefused(page, uploadSessionName, wav);
        await enableRecordingOnUploadSession(page, uploadSessionName);
        await verifyUploadSession(page, uploadSessionName, wav);
        log("all checks passed");
    } catch (err) {
        const shot = path.join(cfg.artifactsDir, "failure.png");
        await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
        console.error(`[recording] ✗ FAILED during "${step}": ${err.message}`);
        console.error(`[recording]   screenshot: ${shot}`);
        process.exitCode = 1;
    } finally {
        if (created && !cfg.keep) {
            await deleteRecordingSession(page, sessionName).catch((err) =>
                log(`cleanup failed: ${err.message}`),
            );
        } else if (created) {
            log(`keeping recording session "${sessionName}"`);
        }
        if (uploadCreated && !cfg.keep) {
            await deleteRecordingSession(page, uploadSessionName).catch((err) =>
                log(`cleanup failed: ${err.message}`),
            );
        } else if (uploadCreated) {
            log(`keeping upload session "${uploadSessionName}"`);
        }
        await browser.close();
    }
}

main();
