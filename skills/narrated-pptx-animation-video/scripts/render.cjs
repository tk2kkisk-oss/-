// index.html をフレームごとに描画して MP4 に書き出す。
// usage: NODE_PATH=<playwright のある node_modules> node tools/render.cjs [out.mp4] [--fps 30] [--workers 6] [--still t1,t2,...]
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const OUT = path.resolve(args[0] && !args[0].startsWith('--') ? args[0] : path.join(ROOT, 'dementia-care-training.mp4'));
const FPS = Number(opt('--fps', 30));
const WORKERS = Number(opt('--workers', 6));
const STILLS = opt('--still', null);
const TMP = path.join(ROOT, '.render-tmp');
const URL = 'file://' + path.join(ROOT, 'index.html') + '?render';
const LEAD = 0.6;

async function openPage(browser) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', e => { console.error('page error:', e.message); process.exit(1); });
  await page.goto(URL);
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(i => i.decode().catch(() => {}))); });
  return page;
}

function run(cmd, a) { execFileSync(cmd, a, { stdio: ['ignore', 'ignore', 'inherit'] }); }

// シーンの長さに合わせて、各スライドの音声を LEAD 秒ずらして無音で埋めたナレーションを作る
function buildAudio(scenes, out) {
  const inputs = [], filters = [];
  scenes.forEach((s, i) => {
    inputs.push('-i', path.join(ROOT, 'assets/audio', String(i + 1).padStart(2, '0') + '.mp3'));
    const ms = Math.round(LEAD * 1000);
    filters.push(`[${i}:a]aresample=48000,aformat=channel_layouts=stereo,adelay=${ms}|${ms},apad,atrim=0:${s.len.toFixed(4)}[a${i}]`);
  });
  const fc = filters.join(';') + ';' + scenes.map((_, i) => `[a${i}]`).join('') + `concat=n=${scenes.length}:v=0:a=1[out]`;
  run('ffmpeg', ['-y', '-loglevel', 'error', ...inputs, '-filter_complex', fc, '-map', '[out]', '-c:a', 'aac', '-b:a', '192k', out]);
}

async function renderRange(browser, from, to, file, label) {
  const page = await openPage(browser);
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', String(FPS), file], { stdio: ['pipe', 'ignore', 'inherit'] });
  const done = new Promise((res, rej) => ff.on('close', c => c === 0 ? res() : rej(new Error('ffmpeg ' + c))));
  let prevSig = null, buf = null, shots = 0;
  for (let f = from; f < to; f++) {
    const sig = await page.evaluate(t => window.renderAt(t), f / FPS);
    if (sig !== prevSig || !buf) { buf = await page.screenshot({ type: 'jpeg', quality: 92 }); prevSig = sig; shots++; }
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if ((f - from) % (FPS * 30) === 0) console.log(`[${label}] ${((f - from) / (to - from) * 100).toFixed(0)}%`);
  }
  ff.stdin.end();
  await done;
  await page.close();
  console.log(`[${label}] done: ${to - from} frames, ${shots} screenshots`);
}

(async () => {
  const browser = await chromium.launch();
  const probe = await openPage(browser);
  const { total, scenes } = await probe.evaluate(() => ({ total: window.TOTAL, scenes: window.SCENE_STARTS }));

  if (STILLS) {
    fs.mkdirSync(path.join(TMP, 'stills'), { recursive: true });
    for (const t of STILLS.split(',').map(Number)) {
      await probe.evaluate(t => window.renderAt(t), t);
      const f = path.join(TMP, 'stills', `t${String(t).padStart(6, '0')}.png`);
      await probe.screenshot({ path: f });
      console.log(f);
    }
    await browser.close();
    return;
  }

  fs.rmSync(TMP, { recursive: true, force: true });
  fs.mkdirSync(TMP, { recursive: true });
  const nFrames = Math.round(total * FPS);
  console.log(`total ${total.toFixed(1)}s, ${nFrames} frames @${FPS}fps, ${WORKERS} workers`);
  const audio = path.join(TMP, 'narration.m4a');
  buildAudio(scenes, audio);
  fs.copyFileSync(audio, path.join(ROOT, 'assets/narration.m4a'));

  const parts = [];
  const per = Math.ceil(nFrames / WORKERS);
  const jobs = [];
  for (let w = 0; w < WORKERS; w++) {
    const from = w * per, to = Math.min(nFrames, from + per);
    if (from >= to) break;
    const file = path.join(TMP, `part${w}.mp4`);
    parts.push(file);
    jobs.push(renderRange(browser, from, to, file, 'w' + w));
  }
  await Promise.all(jobs);
  await browser.close();

  const list = path.join(TMP, 'parts.txt');
  fs.writeFileSync(list, parts.map(p => `file '${p}'`).join('\n'));
  run('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-i', audio,
    '-map', '0:v', '-map', '1:a', '-c', 'copy', '-movflags', '+faststart', '-shortest', OUT]);
  fs.rmSync(TMP, { recursive: true, force: true });
  console.log('wrote', OUT);
})();
