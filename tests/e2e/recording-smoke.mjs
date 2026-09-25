// End-to-end smoke test of the online recording pipeline:
//
//   log in → create an online-recording session → record every prompt in the
//   web recorder → confirm the recordings are imported into the project.
//
// It also covers the failure modes the pipeline has had:
//   - re-recording a prompt: the final take, not an earlier one, is imported
//   - reloading the recorder mid-session: earlier prompts still count as done,
//     so the session can be completed without re-recording them
//
// Runs in the Playwright container via `./visp.py smoketest` (dev mode only:
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
    console.log(`[smoke] ${msg}`);
}

function pass(msg) {
    console.log(`[smoke] ✓ ${msg}`);
}

class SmokeFailure extends Error {}

function fail(msg) {
    throw new SmokeFailure(msg);
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
    // Existing sessions load asynchronously; the add button is disabled until then.
    await page.waitForFunction(
        () => {
            const add = [...document.querySelectorAll("button.form-add-button")].find((b) =>
                b.innerText.includes("Add recording session"),
            );
            return add && !add.disabled;
        },
        null,
        { timeout: 60000 },
    );
    return dialog;
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
    await domClick(dialog.getByRole("button", { name: "Add recording session" }));
    // A new session is inserted first and starts expanded.
    const nameInput = dialog.locator(".session-name-input").first();
    await nameInput.waitFor();
    await nameInput.fill(sessionName);
    // The name control only updates on blur.
    await nameInput.blur();
    await domClick(dialog.locator("mat-radio-button[value=record] input").first());

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
    const header = dialog.locator(".section-header-container h4").getByText(sessionName, { exact: true });
    if ((await header.count()) === 0) {
        return { dialog, text: null };
    }
    await domClick(header);
    await page.waitForTimeout(1000);
    const text = await dialog.innerText();
    const start = text.indexOf(sessionName);
    // The panel ends where the next session header or the add button begins.
    const end = text.indexOf("Add recording session", start);
    return { dialog, text: text.slice(start, end === -1 ? undefined : end) };
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
    // Exactly this session's header row; never fall back to a broader match,
    // or the first trash icon in the dialog belongs to some other session.
    const header = dialog.locator(".section-header-container").filter({
        has: page.locator("h4").getByText(sessionName, { exact: true }),
    });
    if ((await header.count()) !== 1) {
        log(`could not identify the header of "${sessionName}"; leaving it in place`);
        return;
    }
    const trash = header.locator(".itemDeleteBtn .fa-trash-o");
    page.once("dialog", (d) => d.accept());
    await domClick(trash);
    await saveProjectDialog(page, dialog);
    pass(`deleted recording session "${sessionName}"`);
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
// repositories directory mounted (visp.py smoketest does this read-only).
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
    let created = false;
    try {
        await login(page);
        const link = await createRecordingSession(page, sessionName);
        created = true;
        const itemCodes = await recordSession(page, link);
        await waitForImport(page, sessionName, itemCodes.map((c) => c + ".wav"));
        await verifyBundlesOnDisk(page, link, sessionName, itemCodes);
        log("all checks passed");
    } catch (err) {
        const shot = path.join(cfg.artifactsDir, "failure.png");
        await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
        console.error(`[smoke] ✗ FAILED during "${step}": ${err.message}`);
        console.error(`[smoke]   screenshot: ${shot}`);
        process.exitCode = 1;
    } finally {
        if (created && !cfg.keep) {
            await deleteRecordingSession(page, sessionName).catch((err) =>
                log(`cleanup failed: ${err.message}`),
            );
        } else if (created) {
            log(`keeping recording session "${sessionName}"`);
        }
        await browser.close();
    }
}

main();
