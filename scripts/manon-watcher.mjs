#!/usr/bin/env node
/**
 * manon-watcher.mjs — 监控 manon app/ 目录变更，触发 OpenClaw QA agent
 *
 * 用法: node scripts/manon-watcher.mjs
 * 环境变量: GEMINI_API_KEY, HTTPS_PROXY, HTTP_PROXY, NO_PROXY
 */

import chokidar from 'chokidar';
import { execSync, spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const APP_DIR = path.join(ROOT, 'app');

// ── Config ──────────────────────────────────────────────
const DEBOUNCE_MS = 500;
const SESSION_ID = 'manon-qa';
const OPENCLAW_BIN = 'openclaw';

// ── State ───────────────────────────────────────────────
let debounceTimer = null;
let agentRunning = false;
const pendingFiles = new Set();

// ── Helpers ─────────────────────────────────────────────
function getDiff() {
  try {
    const staged = execSync('git diff --cached --stat', { cwd: ROOT, encoding: 'utf-8' });
    const unstaged = execSync('git diff', { cwd: ROOT, encoding: 'utf-8' });
    return (staged + unstaged).trim();
  } catch {
    return '';
  }
}

function getDiffForFiles(files) {
  const paths = [...files].map(f => path.relative(ROOT, f)).join(' ');
  try {
    return execSync(`git diff -- ${paths}`, { cwd: ROOT, encoding: 'utf-8' }).trim();
  } catch {
    return '';
  }
}

function buildPrompt(diff, files) {
  const fileList = [...files].map(f => path.relative(ROOT, f)).join(', ');
  const hasHtml = [...files].some(f => f.endsWith('.html'));
  const hasRouter = [...files].some(f => f.includes('routers'));
  const hasWs = [...files].some(f => f.includes('ws_hub'));
  const hasWorker = [...files].some(f => f.includes('worker') || f.includes('coach'));

  let hint = '先 inspect 检查页面状态';
  if (hasHtml) hint = '页面结构可能变了，inspect 后重点检查布局和交互';
  if (hasRouter) hint = '后端路由变了，用 --check-api 检查 API，再 interact 验证前端是否正常';
  if (hasWs) hint = 'WebSocket 相关变更，检查连接状态和消息收发';
  if (hasWorker) hint = 'Pipeline/Worker 变更，发一条消息验证 pipeline 是否正常启动';

  return [
    `[manon-qa] 文件变更: ${fileList}`,
    '',
    '```diff',
    diff.slice(0, 6000),
    '```',
    '',
    `提示: ${hint}`,
    '',
    '请验证这些变更:',
    '1. 先 inspect 确认页面能正常加载',
    '2. 根据 diff 内容，用 interact 模式在浏览器里实际操作来验证功能',
    '3. 读取 JSON 结果文件分析详细数据',
    '4. 报告实际发生了什么，有没有错误或异常行为',
  ].join('\n');
}

function triggerAgent(diff, files) {
  if (agentRunning) {
    console.log('[watcher] agent 正在运行，跳过本次触发');
    return;
  }

  const prompt = buildPrompt(diff, files);
  console.log(`\n[watcher] 触发 OpenClaw agent (${files.size} 个文件变更)`);
  console.log(`[watcher] 文件: ${[...files].map(f => path.relative(ROOT, f)).join(', ')}`);

  agentRunning = true;
  const child = spawn(OPENCLAW_BIN, [
    'agent',
    '--session-id', SESSION_ID,
    '--skill', 'manon-qa',
    '--message', prompt,
  ], {
    cwd: ROOT,
    stdio: 'inherit',
    shell: true,
    env: { ...process.env },
  });

  child.on('close', (code) => {
    agentRunning = false;
    console.log(`[watcher] agent 退出 (code=${code})`);
  });

  child.on('error', (err) => {
    agentRunning = false;
    console.error(`[watcher] agent 启动失败:`, err.message);
  });
}

// ── Watcher ─────────────────────────────────────────────
function handleChange(filePath) {
  pendingFiles.add(filePath);
  if (debounceTimer) clearTimeout(debounceTimer);

  debounceTimer = setTimeout(() => {
    const files = new Set(pendingFiles);
    pendingFiles.clear();

    const diff = getDiffForFiles(files) || getDiff();
    if (!diff) {
      console.log('[watcher] 无 diff 内容，跳过');
      return;
    }
    triggerAgent(diff, files);
  }, DEBOUNCE_MS);
}

const watcher = chokidar.watch(APP_DIR, {
  ignored: [
    /(^|[\/\\])\../,
    /node_modules/,
    /\.pyc$/,
    /__pycache__/,
    /test-results/,
  ],
  persistent: true,
  ignoreInitial: true,
});

watcher
  .on('change', handleChange)
  .on('add', handleChange)
  .on('ready', () => {
    console.log('[manon-watcher] 监控已启动');
    console.log(`[manon-watcher] 目录: ${APP_DIR}`);
    console.log(`[manon-watcher] 防抖: ${DEBOUNCE_MS}ms`);
    console.log('[manon-watcher] 等待文件变更...\n');
  });

process.on('SIGINT', () => {
  console.log('\n[manon-watcher] 停止监控');
  watcher.close();
  process.exit(0);
});
