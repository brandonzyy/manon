#!/usr/bin/env node
/**
 * manon-test-base.mjs — 浏览器检查 + 交互工具
 *
 * 两种模式:
 *   --inspect          被动检查：截图 + DOM 状态 + 错误收集
 *   --interact '<json>'  主动操作：在浏览器里执行一系列动作
 *   --interact-file f.json  从文件读取操作序列
 *
 * 用法:
 *   node scripts/manon-test-base.mjs --inspect [--quick] [--check-api]
 *   node scripts/manon-test-base.mjs --interact '[{"action":"send-chat","text":"hello"}]'
 *
 * 截图/结果保存到: app/static/test-results/
 */

import { chromium } from 'playwright';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const RESULTS_DIR = path.join(ROOT, 'app', 'static', 'test-results');
const BASE_URL = process.env.MANON_URL || 'http://localhost:3600';

const argv = process.argv.slice(2);
const consoleErrors = [];
const networkFailures = [];
const consoleLog = [];

function stamp() {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
}

async function snap(page, name) {
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const file = path.join(RESULTS_DIR, `${name}_${stamp()}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

function setupCollectors(page) {
  page.on('console', msg => {
    const entry = { type: msg.type(), text: msg.text() };
    if (msg.type() === 'error') consoleErrors.push(entry);
    else consoleLog.push(entry);
  });
  page.on('requestfailed', req => {
    networkFailures.push({ url: req.url(), method: req.method(), error: req.failure()?.errorText });
  });
}

// ── HTML Report Generator ───────────────────────────────
function generateHtmlReport(data) {
  const { mode, screenshots = [], pageState, steps, api } = data;
  const time = new Date().toLocaleString('zh-CN');

  const screenshotHtml = screenshots.map(s => {
    const name = path.basename(s);
    return `<div class="shot"><img src="${name}" /><p>${name}</p></div>`;
  }).join('\n');

  const errHtml = consoleErrors.length
    ? consoleErrors.map(e => `<div class="err">❌ ${esc(e.text)}</div>`).join('\n')
    : '<div class="ok">✓ 无 console 错误</div>';

  const netHtml = networkFailures.length
    ? networkFailures.map(f => `<div class="err">❌ ${f.method} ${esc(f.url)} — ${esc(f.error)}</div>`).join('\n')
    : '<div class="ok">✓ 无网络失败</div>';

  let stateHtml = '';
  if (pageState) {
    const s = pageState;
    stateHtml = `<table>
      <tr><td>WebSocket</td><td>${esc(s.wsStatus.dot)} / ${esc(s.wsStatus.label)}</td></tr>
      <tr><td>Model</td><td>${esc(s.modelIndicator)}</td></tr>
      <tr><td>Project</td><td>${esc(s.projectSelect)}</td></tr>
      <tr><td>Gateway</td><td>${esc(s.gateway)}</td></tr>
      <tr><td>Workers</td><td>${esc(s.workers)}</td></tr>
      <tr><td>Metrics</td><td>E=${esc(s.metrics.entities)} R=${esc(s.metrics.relations)} F=${esc(s.metrics.files)} C=${esc(s.metrics.chunks)}</td></tr>
      <tr><td>Pipeline</td><td>${esc(s.pipelineBanner)}</td></tr>
      <tr><td>Messages</td><td>${s.messageCount}</td></tr>
    </table>`;
  }

  let stepsHtml = '';
  if (steps) {
    stepsHtml = '<table><tr><th>#</th><th>Action</th><th>Result</th><th>Detail</th></tr>' +
      steps.map(s => {
        const icon = s.ok ? '✅' : '❌';
        const detail = s.error || s.text || s.sent || (s.messages ? `${s.messages.length} msgs` : '') || '';
        return `<tr><td>${s.step}</td><td>${s.action}</td><td>${icon}</td><td>${esc(String(detail).slice(0,200))}</td></tr>`;
      }).join('\n') + '</table>';
  }

  const html = `<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Manon 验证报告 — ${time}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;max-width:1200px;margin:0 auto}
  h1{color:#58a6ff;margin-bottom:8px;font-size:22px}
  h2{color:#8b949e;font-size:16px;margin:20px 0 8px;border-bottom:1px solid #21262d;padding-bottom:4px}
  .meta{color:#8b949e;font-size:13px;margin-bottom:16px}
  .err{background:#3d1f28;border-left:3px solid #f85149;padding:8px 12px;margin:4px 0;font-family:monospace;font-size:13px;word-break:break-all}
  .ok{color:#3fb950;padding:8px 0;font-size:14px}
  table{width:100%;border-collapse:collapse;margin:8px 0}
  td,th{border:1px solid #21262d;padding:6px 10px;text-align:left;font-size:13px}
  th{background:#161b22;color:#8b949e}
  .shot{margin:12px 0}
  .shot img{max-width:100%;border:1px solid #30363d;border-radius:6px}
  .shot p{color:#8b949e;font-size:12px;margin-top:4px}
  .badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;font-weight:600}
  .badge.pass{background:#1a3a2a;color:#3fb950}
  .badge.fail{background:#3d1f28;color:#f85149}
</style></head><body>
<h1>Manon 验证报告</h1>
<div class="meta">模式: ${mode} | 时间: ${time} |
  <span class="badge ${consoleErrors.length || networkFailures.length ? 'fail' : 'pass'}">
    ${consoleErrors.length || networkFailures.length ? '有问题' : '正常'}
  </span>
</div>
<h2>Console 错误</h2>${errHtml}
<h2>网络请求</h2>${netHtml}
${stateHtml ? '<h2>页面状态</h2>' + stateHtml : ''}
${stepsHtml ? '<h2>操作步骤</h2>' + stepsHtml : ''}
${screenshotHtml ? '<h2>截图</h2>' + screenshotHtml : ''}
</body></html>`;

  const reportPath = path.join(RESULTS_DIR, 'report.html');
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(reportPath, html);
  return reportPath;
}

function esc(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
// ── DOM State Extraction ────────────────────────────────
async function extractPageState(page) {
  return await page.evaluate(() => {
    const txt = s => { const e = document.querySelector(s); return e ? e.textContent.trim() : null; };
    const vis = s => { const e = document.querySelector(s); if (!e) return 'missing'; const c = getComputedStyle(e); return (c.display === 'none' || c.visibility === 'hidden') ? 'hidden' : 'visible'; };
    const cls = s => { const e = document.querySelector(s); return e ? [...e.classList].join(' ') : null; };
    return {
      title: document.title,
      wsStatus: { dot: cls('#wsDot'), label: txt('#wsLabel') },
      modelIndicator: txt('#modelIndicator'),
      projectSelect: txt('#projectSelect'),
      metrics: { entities: txt('#statEntities'), relations: txt('#statRelations'), files: txt('#statFiles'), chunks: txt('#statChunks') },
      gateway: txt('#statGateway'), workers: txt('#statWorkers'),
      pipelineBanner: txt('#pipelineBanner'),
      pipelineSteps: [...document.querySelectorAll('#pipelineSteps .ps')].map(e => ({ label: e.textContent.trim(), classes: [...e.classList].join(' ') })),
      messageCount: document.querySelectorAll('#messages .msg').length,
      welcomeVisible: vis('#cliWelcome'), thinkingVisible: vis('#thinking'),
      queryCount: txt('#queryCount'), reportCount: txt('#reportCount'),
      setupModalVisible: vis('#setupModal'), settingsModalVisible: vis('#settingsModal'), welcomeOverlayVisible: vis('#welcomeOverlay'),
      bodyClasses: [...document.body.classList].join(' '),
    };
  });
}

// ── Interact: Action Executors ──────────────────────────
async function execAction(page, step) {
  const { action } = step;
  const timeout = step.timeout || 10000;

  switch (action) {
    case 'screenshot': {
      const file = await snap(page, step.name || 'interact');
      return { ok: true, screenshot: file };
    }
    case 'click': {
      await page.click(step.selector, { timeout });
      return { ok: true };
    }
    case 'type': {
      await page.fill(step.selector, step.text);
      return { ok: true };
    }
    case 'press': {
      await page.keyboard.press(step.key);
      return { ok: true };
    }
    case 'wait': {
      await page.waitForSelector(step.selector, { timeout, state: step.state || 'visible' });
      return { ok: true };
    }
    case 'wait-time': {
      await page.waitForTimeout(step.ms || 1000);
      return { ok: true };
    }
    case 'read': {
      const text = await page.$eval(step.selector, el => el.textContent.trim());
      return { ok: true, text };
    }
    case 'read-html': {
      const html = await page.$eval(step.selector, el => el.innerHTML);
      return { ok: true, html: html.slice(0, step.maxLen || 3000) };
    }
    case 'read-all': {
      const items = await page.$$eval(step.selector, els => els.map(e => e.textContent.trim()));
      return { ok: true, items };
    }
    case 'eval': {
      const result = await page.evaluate(step.js);
      return { ok: true, result };
    }
    case 'inspect': {
      const state = await extractPageState(page);
      return { ok: true, state };
    }
    // ── 便捷操作：manon 专用 ──
    case 'send-chat': {
      // 在输入框输入文字并发送
      await page.fill('#input', step.text);
      await page.keyboard.press('Enter');
      return { ok: true, sent: step.text };
    }
    case 'wait-response': {
      // 等待新的 manon 回复出现
      const beforeCount = await page.$$eval('#messages .msg.manon', els => els.length);
      const maxWait = step.timeout || 60000;
      const poll = 500;
      let elapsed = 0;
      while (elapsed < maxWait) {
        await page.waitForTimeout(poll);
        elapsed += poll;
        const nowCount = await page.$$eval('#messages .msg.manon', els => els.length);
        if (nowCount > beforeCount) {
          // 再等一下让流式输出完成
          await page.waitForTimeout(step.settle || 2000);
          return { ok: true, newResponses: nowCount - beforeCount };
        }
      }
      return { ok: false, error: `No new response after ${maxWait}ms` };
    }
    case 'read-messages': {
      // 读取聊天消息
      const msgs = await page.$$eval('#messages .msg', els => els.map(e => ({
        role: e.classList.contains('user') ? 'user' : e.classList.contains('manon') ? 'manon' : 'system',
        text: e.textContent.trim().slice(0, 1000),
      })));
      const n = step.last || msgs.length;
      return { ok: true, messages: msgs.slice(-n) };
    }
    case 'wait-pipeline': {
      // 等待 pipeline 进入指定状态
      const target = step.state || 'done';
      const maxWait = step.timeout || 120000;
      const poll = 1000;
      let elapsed = 0;
      while (elapsed < maxWait) {
        const banner = await page.$eval('#pipelineBanner', el => el.textContent.trim()).catch(() => '');
        if (banner.toLowerCase().includes(target)) return { ok: true, state: banner };
        await page.waitForTimeout(poll);
        elapsed += poll;
      }
      const final = await page.$eval('#pipelineBanner', el => el.textContent.trim()).catch(() => '');
      return { ok: false, error: `Pipeline did not reach "${target}" after ${maxWait}ms`, state: final };
    }
    case 'check-errors': {
      // 返回当前收集到的所有错误
      return { ok: true, consoleErrors: [...consoleErrors], networkFailures: [...networkFailures] };
    }
    default:
      return { ok: false, error: `Unknown action: ${action}` };
  }
}
// ── Interact Mode ───────────────────────────────────────
async function runInteract(steps, live = false) {
  console.log(`[manon] ${live ? 'LIVE' : 'interact'} 模式: ${steps.length} 个操作\n`);

  const browser = await chromium.launch({
    headless: !live,
    ...(live ? { slowMo: 300, args: ['--start-maximized'] } : {}),
  });
  const context = await browser.newContext({
    viewport: live ? null : { width: 1440, height: 900 },
    ...(live ? { noViewport: true } : {}),
  });
  const page = await context.newPage();
  setupCollectors(page);

  // 先导航到页面
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(2000); // 等 WS 连接

  const results = [];
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    console.log(`  [${i + 1}/${steps.length}] ${step.action}${step.text ? ': ' + step.text.slice(0, 50) : ''}${step.selector ? ' → ' + step.selector : ''}`);
    try {
      const r = await execAction(page, step);
      results.push({ step: i + 1, action: step.action, ...r });
      if (!r.ok) console.log(`    ⚠ ${r.error}`);
    } catch (e) {
      results.push({ step: i + 1, action: step.action, ok: false, error: e.message });
      console.log(`    ❌ ${e.message}`);
      if (step.stopOnError !== false) {
        console.log('    停止执行（后续步骤跳过）');
        break;
      }
    }
  }

  // 写结果
  const output = {
    timestamp: new Date().toISOString(),
    url: BASE_URL,
    mode: live ? 'live' : 'interact',
    steps: results,
    consoleErrors,
    networkFailures,
  };

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  const jsonPath = path.join(RESULTS_DIR, 'latest-interact.json');
  fs.writeFileSync(jsonPath, JSON.stringify(output, null, 2));

  const okCount = results.filter(r => r.ok).length;
  const failCount = results.filter(r => !r.ok).length;

  console.log('\n' + '═'.repeat(60));
  console.log(`  操作: ${okCount} 成功, ${failCount} 失败`);
  if (consoleErrors.length) {
    console.log(`  ❌ Console 错误: ${consoleErrors.length}`);
    consoleErrors.forEach(e => console.log(`     ${e.text}`));
  }
  if (networkFailures.length) {
    console.log(`  ❌ 网络失败: ${networkFailures.length}`);
    networkFailures.forEach(f => console.log(`     ${f.method} ${f.url}`));
  }
  console.log(`  结果: ${jsonPath}`);
  console.log('═'.repeat(60));

  if (live) {
    // 浏览器保持打开，用户自己观察
    console.log('\n  ✦ 浏览器已打开，流程执行完毕。请在浏览器中检查页面。');
    console.log('  ✦ 按 Ctrl+C 关闭浏览器退出。\n');
    // 保持进程存活
    await new Promise(() => {});
  } else {
    const finalScreenshot = await snap(page, 'final');
    output.finalScreenshot = finalScreenshot;
    fs.writeFileSync(jsonPath, JSON.stringify(output, null, 2));
    generateHtmlReport({ mode: 'interact', screenshots: [finalScreenshot], steps: results });
    console.log(`  报告: ${BASE_URL}/static/test-results/report.html`);
    await browser.close();
    process.exit(failCount > 0 || consoleErrors.length > 0 ? 1 : 0);
  }
}
// ── Inspect Mode ────────────────────────────────────────
async function runInspect() {
  const quickMode = argv.includes('--quick');
  const checkApi = argv.includes('--check-api');
  console.log(`[manon] inspect 模式${quickMode ? ' (快速)' : ''}${checkApi ? ' + API' : ''}\n`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  setupCollectors(page);

  const report = { timestamp: new Date().toISOString(), url: BASE_URL, mode: 'inspect', screenshots: [] };

  try {
    const resp = await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 15000 });
    report.httpStatus = resp.status();
    report.httpOk = resp.ok();
    await page.waitForTimeout(2000);
    report.screenshots.push(await snap(page, 'page'));
    if (!quickMode) report.pageState = await extractPageState(page);
  } catch (e) {
    report.loadError = e.message;
    try { report.screenshots.push(await snap(page, 'error')); } catch {}
  }

  if (checkApi) {
    const endpoints = ['/api/health', '/api/settings'];
    report.api = [];
    for (const ep of endpoints) {
      try {
        const r = await fetch(`${BASE_URL}${ep}`);
        const body = await r.text();
        let json; try { json = JSON.parse(body); } catch {}
        report.api.push({ endpoint: ep, status: r.status, ok: r.ok, body: json || body.slice(0, 500) });
      } catch (e) { report.api.push({ endpoint: ep, error: e.message }); }
    }
  }

  report.consoleErrors = consoleErrors;
  report.networkFailures = networkFailures;
  await browser.close();

  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(path.join(RESULTS_DIR, 'latest-inspect.json'), JSON.stringify(report, null, 2));

  generateHtmlReport({ mode: 'inspect', screenshots: report.screenshots, pageState: report.pageState, api: report.api });

  // 终端摘要
  console.log('═'.repeat(60));
  console.log(`  HTTP: ${report.httpStatus || 'N/A'}`);
  if (consoleErrors.length) { console.log(`  ❌ Console 错误: ${consoleErrors.length}`); consoleErrors.forEach(e => console.log(`     ${e.text}`)); }
  else console.log('  ✓ 无 console 错误');
  if (networkFailures.length) { console.log(`  ❌ 网络失败: ${networkFailures.length}`); networkFailures.forEach(f => console.log(`     ${f.method} ${f.url}`)); }
  else console.log('  ✓ 无网络失败');
  if (report.pageState) {
    const s = report.pageState;
    console.log(`  WS: [${s.wsStatus.dot}] ${s.wsStatus.label} | Model: ${s.modelIndicator} | Gateway: ${s.gateway}`);
  }
  console.log(`  截图: ${report.screenshots.join(', ')}`);
  console.log(`  报告: ${BASE_URL}/static/test-results/report.html`);
  console.log('═'.repeat(60) + '\n');

  process.exit(consoleErrors.length > 0 || networkFailures.length > 0 || !report.httpOk ? 1 : 0);
}

// ── Entry Point ─────────────────────────────────────────
const interactIdx = argv.indexOf('--interact');
const interactFileIdx = argv.indexOf('--interact-file');
const liveIdx = argv.indexOf('--live');
const isLive = liveIdx !== -1;

if (interactIdx !== -1 || interactFileIdx !== -1 || isLive) {
  let steps;
  if (interactIdx !== -1 && argv[interactIdx + 1]) {
    steps = JSON.parse(argv[interactIdx + 1]);
  } else if (interactFileIdx !== -1 && argv[interactFileIdx + 1]) {
    steps = JSON.parse(fs.readFileSync(argv[interactFileIdx + 1], 'utf-8'));
  } else if (isLive && !argv[liveIdx + 1]) {
    // --live without steps: just open browser and stay
    steps = [{ action: 'inspect' }];
  } else if (isLive && argv[liveIdx + 1]) {
    steps = JSON.parse(argv[liveIdx + 1]);
  } else {
    console.error('需要提供操作步骤 JSON'); process.exit(2);
  }
  runInteract(steps, isLive);
} else {
  runInspect();
}
