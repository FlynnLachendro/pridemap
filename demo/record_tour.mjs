// Records the in-app tour as a video (backup for the live demo).
// usage: cd demo && npm i playwright && npx playwright install chromium && node record_tour.mjs
// Needs the app running (./dev.sh). Output: demo/videos/<timestamp>.webm (convert with ffmpeg if you want mp4).
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3000";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: "videos", size: { width: 1440, height: 900 } } });
const page = await ctx.newPage();
await page.goto(`${BASE}/`);
await page.evaluate(() => { localStorage.removeItem("pridemap.compare"); localStorage.removeItem("pridemap.confirmed"); });
await page.goto(`${BASE}/?tour=0`);

const TOTAL = 22;
for (let i = 0; i < TOTAL; i++) {
  await page.waitForSelector(".driver-popover", { timeout: 120000 });
  await page.waitForTimeout(4500);                      // let the viewer read
  const next = page.locator(".driver-popover-next-btn");
  if (await next.count()) await next.click();
  await page.waitForTimeout(1500);
}
await page.waitForTimeout(3000);
await ctx.close();                                      // flushes the video
await browser.close();
console.log("video written to demo/videos/");
