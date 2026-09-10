const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const root = __dirname;
(async () => {
  const browser = await chromium.launch({headless:true});
  const checks = [];
  for (const width of [1440,390]) for (const file of ['guideline.html','architecture.html']) {
    const page = await browser.newPage({viewport:{width,height:1000}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.goto('file://'+path.join(root,file));
    await page.evaluate(()=>document.fonts.ready);
    const state=await page.evaluate(()=>({
      overflow:document.documentElement.scrollWidth>innerWidth,
      brokenHash:[...document.querySelectorAll('a[href^="#"]')].filter(a=>!document.getElementById(decodeURIComponent(a.hash.slice(1)))).map(a=>a.hash),
      rules:document.querySelectorAll('.rule').length,
      metrics:document.querySelectorAll('.metric').length,
      hash:document.querySelector('.doc-footer').textContent.match(/[a-f0-9]{64}/)?.[0],
      links:[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')).filter(h=>!h.startsWith('#')&&!/^https?:/.test(h))
    }));
    const missing=state.links.filter(h=>!fs.existsSync(path.resolve(root,h.split('#')[0])));
    assert(!state.overflow);assert.equal(state.brokenHash.length,0);assert.equal(errors.length,0);assert.equal(missing.length,0);
    assert.equal(state.hash,JSON.parse(fs.readFileSync(path.join(root,'validation.json'))).policy_sha256);
    if(file==='guideline.html') {assert.equal(state.rules,34);assert.equal(state.metrics,45);}
    checks.push({file,width,overflow:state.overflow,brokenHash:state.brokenHash,errors,missing,policyHashMatch:true});
    if(width===1440) await page.screenshot({path:path.join(root,file==='guideline.html'?'preview-guideline.png':'preview-architecture.png')});
    if(width===390&&file==='guideline.html') await page.screenshot({path:path.join(root,'preview-mobile.png')});
    if(width===1440&&file==='guideline.html') {
      await page.locator('#ruleSearch').fill('출석');assert.equal(await page.locator('.rule:not(.hidden)').count(),1);
      await page.locator('#ruleSearch').fill('');await page.locator('#category').selectOption('director_election');assert.equal(await page.locator('.rule:not(.hidden)').count(),5);
      await page.locator('#category').selectOption('');
      await page.locator('#attendance').evaluate(e=>{e.value=75;e.dispatchEvent(new Event('input',{bubbles:true}));});assert.equal(await page.locator('#demoVote').textContent(),'이 규칙은 미해당');
      await page.locator('#attendance').evaluate(e=>{e.value=62.5;e.dispatchEvent(new Event('input',{bubbles:true}));});
      await page.locator('#exception').selectOption('yes');assert.equal(await page.locator('#demoVote').textContent(),'예외 검토');
      await page.locator('#exception').selectOption('unknown');assert.equal(await page.locator('#demoVote').textContent(),'소명 확인 필요');
      await page.locator('#rule-B01').evaluate(e=>e.open=true);await page.locator('#rule-B01').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(root,'preview-rule.png')});
      checks.push({interactions:'search/category/75 percent boundary/accepted exception/unknown exception',passed:true});
    }
    await page.close();
  }
  const diagram=await browser.newPage({viewport:{width:1600,height:1120}});
  await diagram.goto('file://'+path.join(root,'flowchart.svg'));await diagram.evaluate(()=>document.fonts.ready);
  await diagram.screenshot({path:path.join(root,'flowchart.png')});
  await browser.close();
  fs.writeFileSync(path.join(root,'visual-checks.json'),JSON.stringify(checks,null,2)+'\n');
  process.stdout.write(JSON.stringify({status:'passed',viewports:[1440,390],htmlFiles:2,checks:checks.length})+'\n');
})();
