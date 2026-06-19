const fs = require('fs');
const puppeteer = require('puppeteer');

(async () => {
  const [, , svgPath, outPath, scaleArg] = process.argv;
  const scale = parseFloat(scaleArg || '2.5');
  const svg = fs.readFileSync(svgPath, 'utf8');
  const m = svg.match(/width="(\d+)"\s+height="(\d+)"/);
  const w = parseInt(m[1], 10), h = parseInt(m[2], 10);
  const browser = await puppeteer.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h, deviceScaleFactor: scale });
  await page.setContent(
    `<!DOCTYPE html><html><head><meta charset="utf-8">
     <style>html,body{margin:0;padding:0}</style></head>
     <body>${svg}</body></html>`,
    { waitUntil: 'networkidle0' }
  );
  await page.evaluate(() => document.fonts && document.fonts.ready);
  const el = await page.$('svg');
  await el.screenshot({ path: outPath, omitBackground: false });
  await browser.close();
  console.log(`PNG written: ${outPath} (${w}x${h} @${scale}x)`);
})();
