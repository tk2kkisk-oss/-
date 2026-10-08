"""ナレーション音声の無音区間と台本の句読点を対応づけ、文字位置→秒の対応表を作る。
usage: python3 align.py <pptx展開dir> <out timing.js>"""
import json, re, subprocess, sys, os

def notes(xdir, i):
    s = open(f'{xdir}/ppt/notesSlides/notesSlide{i}.xml', encoding='utf8').read()
    paras = re.findall(r'<a:p>(.*?)</a:p>', s, re.S)
    out = []
    for p in paras:
        t = ''.join(re.findall(r'<a:t>([^<]*)</a:t>', p))
        if t.strip() and not t.strip().isdigit():
            out.append(t.strip())
    return ''.join(out)

def silences(mp3):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', mp3, '-af', 'silencedetect=noise=-35dB:d=0.18', '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r'silence_start: ([\d.]+)', r)]
    en = [float(x) for x in re.findall(r'silence_end: ([\d.]+)', r)]
    dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp3],
                               capture_output=True, text=True).stdout)
    sil = list(zip(st, en + [dur] * (len(st) - len(en))))
    return sil, dur

def align(text, sil, dur):
    # 句読点で区切ったチャンク
    chunks, cur = [], ''
    for ch in text:
        cur += ch
        if ch in '、。？！':
            chunks.append(cur); cur = ''
    if cur: chunks.append(cur)
    lens = [len(re.sub(r'[、。？！「」]', '', c)) or 1 for c in chunks]
    # 発話の始まりと終わり
    s0 = sil[0][1] if sil and sil[0][0] < 0.05 else 0.0
    s1 = sil[-1][0] if sil and sil[-1][1] >= dur - 0.05 else dur
    inner = [s for s in sil if s[0] > s0 + 0.05 and s[1] < s1 - 0.05]
    # 境界点: 0 = 発話開始, 1..M = 内部の無音, M+1 = 発話終了
    pts = [(s0, s0)] + inner + [(s1, s1)]
    M = len(pts) - 1
    K = len(chunks)
    speech = (s1 - s0) - sum(b - a for a, b in inner)
    rate = speech / sum(lens)
    INF = 1e18
    dp = [[INF] * (M + 1) for _ in range(K + 1)]
    bk = [[None] * (M + 1) for _ in range(K + 1)]
    dp[0][0] = 0
    for k in range(1, K + 1):
        for m in range(1, M + 1):
            best = INF
            for k2 in range(max(0, k - 4), k):
                for m2 in range(max(0, m - 4), m):
                    if dp[k2][m2] >= INF: continue
                    t = pts[m][0] - pts[m2][1]
                    skipped_sil = sum(pts[j][1] - pts[j][0] for j in range(m2 + 1, m))
                    t -= skipped_sil
                    exp = rate * sum(lens[k2:k])
                    c = (t - exp) ** 2 / (exp + 0.5)
                    c += 3.0 * skipped_sil
                    for j in range(k2, k - 1):
                        c += 1.5 if chunks[j][-1] == '。' else 0.25
                    if dp[k2][m2] + c < best:
                        best = dp[k2][m2] + c; bk[k][m] = (k2, m2)
            dp[k][m] = best
    # 復元
    k, m = K, M
    bounds = []  # (chunk_index_end, start_time_of_group_end, next_start)
    while k > 0:
        k2, m2 = bk[k][m]
        bounds.append((k2, k, pts[m2][1], pts[m][0]))
        k, m = k2, m2
    bounds.reverse()
    # 文字位置→時刻の対応点
    starts = [0]
    for c in chunks: starts.append(starts[-1] + len(c))
    mapping = []
    for k2, k, t0, t1 in bounds:
        mapping.append([starts[k2], round(t0, 3)])
        mapping.append([starts[k] - 0.001, round(t1, 3)])
    return chunks, mapping

def main():
    xdir, out = sys.argv[1], sys.argv[2]
    res = []
    for i in range(1, 17):
        mp3 = f'{xdir}/ppt/media/media{i}.mp3'
        text = notes(xdir, i)
        sil, dur = silences(mp3)
        chunks, mapping = align(text, sil, dur)
        res.append({'slide': i, 'duration': round(dur, 3), 'text': text, 'map': mapping})
        print(i, round(dur, 1), len(chunks), 'chunks', file=sys.stderr)
    body = json.dumps(res, ensure_ascii=False, indent=1)
    with open(out, 'w', encoding='utf8') as f:
        f.write('window.TIMING = ' + body + ';\n' if out.endswith('.js') else body)

if __name__ == '__main__':
    main()
