# 認知症ケア研修 アニメーション動画

「薬の前に、整えられることがある」（掛川東病院 認知症ケア研修）のナレーション付きスライド（narrated.pptx）を、
白×ロイヤルブルーのアニメーション動画にしたものです。

- `dementia-care-training.mp4` … 完成動画（1920×1080 / 30fps / 約10分、字幕付き）
- `index.html` … 動画の元になるアニメーション。ブラウザで開くと、音声付きでプレビュー再生できます
- `timing.js` … ナレーション音声と台本の対応表（どの言葉が何秒目に読まれるか）
- `assets/` … ナレーション音声（PPTXから抽出）、フォント、アンケートQRコード

## しくみ

各要素に `data-in="台本のことば"` を書くと、ナレーションがそのことばを読み上げるタイミングで表示されます。
`data-hl` / `data-hl-end` は、読み上げている間だけ枠を強調します。字幕は台本の句点ごとに自動で切り替わります。

## 作り直す手順

```sh
# 1. PPTX を展開して、ナレーションと台本の対応表を作る（ナレーションを差し替えたとき）
unzip narrated.pptx -d pptx
cp pptx/ppt/media/media{1..16}.mp3 assets/audio/   # 01.mp3〜16.mp3 にリネーム
python3 tools/align.py pptx timing.js

# 2. 動画を書き出す（Playwright + ffmpeg）
node tools/render.cjs dementia-care-training.mp4 --workers 4
# 確認用の静止画だけ: node tools/render.cjs --still 10,60,120
```
