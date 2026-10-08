"""ナレーション付きPPTXから、アニメーション動画プロジェクトの下準備をする。

usage: python3 -I prepare.py <input.pptx> <project_dir> [--silent-sec 6]

やること:
  1. PPTXを project_dir/.pptx に展開（スライド順は presentation.xml に従う）
  2. 各スライドのナレーション音声を assets/audio/01.mp3 ... に書き出す（音声なしのスライドは無音）
  3. ノート（台本）と無音区間を対応づけ、timing.js（文字位置→秒）を作る
  4. スライド本文・台本・尺の一覧 slides.md を作る（シーンを書くときの材料）
  5. template.html → index.html、render.cjs → tools/、フォントを assets/fonts/ に用意
"""
import json, os, re, shutil, subprocess, sys, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

def rels(path):
    if not os.path.exists(path): return {}
    s = open(path, encoding='utf8').read()
    out = {}
    for m in re.finditer(r'<Relationship\b([^>]*)/?>', s):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        out[a.get('Id')] = (a.get('Type', ''), a.get('Target', ''))
    return out

def slide_order(x):
    pres = open(f'{x}/ppt/presentation.xml', encoding='utf8').read()
    r = rels(f'{x}/ppt/_rels/presentation.xml.rels')
    ids = re.findall(r'<p:sldId\b[^>]*r:id="([^"]+)"', pres)
    return [os.path.normpath(os.path.join(x, 'ppt', r[i][1])) for i in ids]

def texts(xml_path, skip_digits=False):
    if not xml_path or not os.path.exists(xml_path): return []
    s = open(xml_path, encoding='utf8').read()
    out = []
    for p in re.findall(r'<a:p>(.*?)</a:p>', s, re.S):
        t = ''.join(re.findall(r'<a:t>([^<]*)</a:t>', p)).strip()
        if t and not (skip_digits and t.isdigit()): out.append(t)
    return out

def unescape(t):
    return t.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&apos;', "'").replace('&amp;', '&')

def duration(f):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f],
                                capture_output=True, text=True).stdout)

def silences(f, dur):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', f, '-af', 'silencedetect=noise=-35dB:d=0.18', '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    st = [float(v) for v in re.findall(r'silence_start: ([\d.]+)', r)]
    en = [float(v) for v in re.findall(r'silence_end: ([\d.]+)', r)]
    return list(zip(st, en + [dur] * (len(st) - len(en))))

def align(text, sil, dur):
    """句読点で区切ったチャンクを無音区間に割り当てる（尺と文字数の比が揃うようにDP）"""
    chunks, cur = [], ''
    for ch in text:
        cur += ch
        if ch in '、。？！?!.,': chunks.append(cur); cur = ''
    if cur: chunks.append(cur)
    if not chunks: return [[0, 0.0], [1, round(dur, 3)]]
    lens = [len(re.sub(r'[、。？！「」?!.,\s]', '', c)) or 1 for c in chunks]
    s0 = sil[0][1] if sil and sil[0][0] < 0.05 else 0.0
    s1 = sil[-1][0] if sil and sil[-1][1] >= dur - 0.05 else dur
    inner = [s for s in sil if s[0] > s0 + 0.05 and s[1] < s1 - 0.05]
    pts = [(s0, s0)] + inner + [(s1, s1)]
    M, K = len(pts) - 1, len(chunks)
    speech = max(0.1, (s1 - s0) - sum(b - a for a, b in inner))
    rate = speech / sum(lens)
    INF = 1e18
    dp = [[INF] * (M + 1) for _ in range(K + 1)]
    bk = [[None] * (M + 1) for _ in range(K + 1)]
    dp[0][0] = 0
    for k in range(1, K + 1):
        for m in range(1, M + 1):
            for k2 in range(max(0, k - 4), k):
                for m2 in range(max(0, m - 4), m):
                    if dp[k2][m2] >= INF: continue
                    skipped = sum(pts[j][1] - pts[j][0] for j in range(m2 + 1, m))
                    t = pts[m][0] - pts[m2][1] - skipped
                    exp = rate * sum(lens[k2:k])
                    c = dp[k2][m2] + (t - exp) ** 2 / (exp + 0.5) + 3.0 * skipped
                    c += sum(1.5 if chunks[j][-1] in '。.!?！？' else 0.25 for j in range(k2, k - 1))
                    if c < dp[k][m]: dp[k][m] = c; bk[k][m] = (k2, m2)
    if dp[K][M] >= INF:  # 無音が少なすぎるときは単純な比例配分
        return [[0, round(s0, 3)], [len(text), round(s1, 3)]]
    starts = [0]
    for c in chunks: starts.append(starts[-1] + len(c))
    k, m, mp = K, M, []
    while k > 0:
        k2, m2 = bk[k][m]
        mp = [[starts[k2], round(pts[m2][1], 3)], [starts[k] - 0.001, round(pts[m][0], 3)]] + mp
        k, m = k2, m2
    return mp

def fetch_fonts(dst):
    os.makedirs(dst, exist_ok=True)
    try:
        css = urllib.request.urlopen('https://fonts.googleapis.com/css2?family=Zen+Kaku+Gothic+New:wght@500;700;900', timeout=30).read().decode()
        for w, url in re.findall(r'font-weight: (\d+);\s*src: url\(([^)]+\.ttf)\)', css):
            out = f'{dst}/ZenKakuGothicNew-{w}.ttf'
            if not os.path.exists(out): urllib.request.urlretrieve(url, out)
        return True
    except Exception as e:
        print('WARN: フォントを取得できませんでした（システムの日本語フォントで代用されます）:', e, file=sys.stderr)
        return False

def main():
    args = sys.argv[1:]
    if len(args) < 2: sys.exit(__doc__)
    pptx, proj = os.path.abspath(args[0]), os.path.abspath(args[1])
    silent = float(args[args.index('--silent-sec') + 1]) if '--silent-sec' in args else 6.0
    x = os.path.join(proj, '.pptx')
    shutil.rmtree(x, ignore_errors=True)
    zipfile.ZipFile(pptx).extractall(x)
    for d in ('assets/audio', 'assets/media', 'tools'): os.makedirs(os.path.join(proj, d), exist_ok=True)

    timing, md = [], ['# スライド一覧（シーン作成用の材料）', '']
    for n, sp in enumerate(slide_order(x), 1):
        r = rels(os.path.join(os.path.dirname(sp), '_rels', os.path.basename(sp) + '.rels'))
        targets = [os.path.normpath(os.path.join(os.path.dirname(sp), t)) for _, (ty, t) in r.items()]
        audio = next((t for t in targets if re.search(r'\.(mp3|m4a|wav|wma|aac)$', t, re.I)), None)
        images = [t for t in targets if re.search(r'\.(png|jpe?g|gif|svg)$', t, re.I)]
        notes = next((t for (ty, _), t in zip(r.values(), targets) if ty.endswith('/notesSlide')), None)
        out = os.path.join(proj, 'assets/audio', f'{n:02d}.mp3')
        if audio and os.path.exists(audio):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', audio, '-ac', '2', '-ar', '48000', '-c:a', 'libmp3lame', '-q:a', '2', out], check=True)
        else:
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', str(silent), '-c:a', 'libmp3lame', out], check=True)
        dur = duration(out)
        script = unescape(''.join(texts(notes, skip_digits=True)))
        mp = align(script, silences(out, dur), dur) if audio else [[0, 0.0], [max(1, len(script)), round(dur, 3)]]
        timing.append({'slide': n, 'duration': round(dur, 3), 'text': script, 'map': mp, 'hasAudio': bool(audio)})
        for im in images:
            shutil.copy(im, os.path.join(proj, 'assets/media', f'slide{n:02d}_' + os.path.basename(im)))
        body = [unescape(t) for t in texts(sp)]
        md += [f'## {n:02d}  （{dur:.1f}秒{"" if audio else "・音声なし"}）', '',
               '**スライド本文:** ' + ' / '.join(body), '',
               '**台本（data-in にはこの文字列の一部をそのまま使う）:**', '', script or '（なし）', '',
               '**画像:** ' + (', '.join(f'assets/media/slide{n:02d}_' + os.path.basename(i) for i in images) or 'なし'), '']

    with open(os.path.join(proj, 'timing.js'), 'w', encoding='utf8') as f:
        f.write('window.TIMING = ' + json.dumps(timing, ensure_ascii=False, indent=1) + ';\n')
    open(os.path.join(proj, 'slides.md'), 'w', encoding='utf8').write('\n'.join(md))
    if not os.path.exists(os.path.join(proj, 'index.html')):
        shutil.copy(os.path.join(SKILL, 'assets/template.html'), os.path.join(proj, 'index.html'))
    shutil.copy(os.path.join(HERE, 'render.cjs'), os.path.join(proj, 'tools/render.cjs'))
    fetch_fonts(os.path.join(proj, 'assets/fonts'))
    total = sum(t['duration'] for t in timing)
    print(f'{len(timing)} slides, narration {total/60:.1f} min -> {proj}')
    print('next: slides.md を読み、index.html の <section class="scene"> をスライド数ぶん書き直す')

if __name__ == '__main__':
    main()
