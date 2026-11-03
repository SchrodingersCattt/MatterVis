"use strict";

// Launch the runner's Chrome through Playwright. Used by the scheduled
// compatibility-drift workflow, not by the merge gate.
const { chromium } = require("playwright-core");

(async () => {
  const executablePath = process.env.CHROME_PATH || "google-chrome";
  const browser = await chromium.launch({
    executablePath,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.goto("about:blank");
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
