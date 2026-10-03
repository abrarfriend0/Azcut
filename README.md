# Az Cut

Narration audio + images/clips se **automatically synced video** banane wala software.
Aap audio dein, images dein, script mein nishan lagayein — app har image ko theek
us jumle par lagayegi jahan aap ne kaha, aur MP4 video tayyar kar degi.

Sab kuch aap ke apne computer par chalta hai — files kahin upload nahi hotin.

## Zaroori cheezein (ek baar)

- **Python 3.10 ya naya** — https://www.python.org/downloads/
  (Windows par install ke waqt **"Add python.exe to PATH"** tick zaroor lagayein)
- **ffmpeg** — video banane ke liye
  - Windows: `winget install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - macOS: `brew install ffmpeg`
- **Microsoft Visual C++ Redistributable (x64)** — Windows par awaz sunne wale
  hisse ke liye zaroori hai. `install.bat` ye khud install karne ki koshish
  karta hai; agar masla ho to khud yahan se lein:
  https://aka.ms/vs/17/release/vc_redist.x64.exe

## Setup (sirf pehli baar)

**Windows:** `install.bat` par double-click karein.
**Mac/Linux:** terminal mein `./install.sh` chalayein.

Ye virtual environment banayega, libraries install karega, aur voice model
download karega (~150MB, sirf pehli baar). 3-5 minute lag sakte hain.

## Chalana

**Windows (browser):** `start.bat` par double-click karein — browser khud khul
jayega: **http://127.0.0.1:5000** (Band karne ke liye kaali window mein `Ctrl+C`.)

**Windows (desktop app):** `AzCut.bat` par double-click karein — Az Cut apni
**apni window** mein khulega, browser ki zaroorat nahi. Desktop par shortcut
banane ke liye: `AzCut.bat` par **right-click → Send to → Desktop (create
shortcut)**. Phir shortcut par right-click → **Properties → Change Icon** →
`icon.ico` select karein, aur naam badal kar **Az Cut** rakh dein.

**Mac/Linux:** `./start.sh` chalayein.

> Note: `AzCut.bat` (desktop mode) ke liye `install.bat` ek baar dobara
> chalayein taake nayi library (pywebview) install ho jaye.

## Istemal — 4 steps

1. **Audio upload karein** — narration wali file (MP3/WAV/M4A). App use sun kar
   har lafz ka waqt note kar legi aur transcript dikhayegi.
2. **Images add karein** — jis tarteeb mein aani chahiye. ◀ ▶ se tarteeb
   badlein, ✕ se hatayein. Chhoti video clips bhi chalengi (khud trim ho jayengi).
3. **Script mein nishan lagayein** — har image ke shuru hone wale jumle se pehle
   `[img1]`, `[img2]`… likhein. **Auto-draft** ka button dabayein to app khud
   ibtedai nishan laga degi — phir unhein adjust kar lein. **Check cues** se
   dekh lein ke har image kitne second par aayegi.
4. **Build video** dabayein — thodi der mein video preview aur **Download MP4**
   ka button mil jayega.

### Cues ki misal

```
[img1] Welcome to the quick tour of our new analytics platform...
[img2] Next, the reports section lets you dig deeper...
[img3] Finally, settings and exports...
```

`[img1]` wali image us jumle ke shuru hone par aayegi, `[img2]` wali agle
nishan tak rahegi, waghera. Aakhri image audio ke end tak chalegi.

## Style (Step 4)

- **Transition** — images ke darmiyan tabdeeli: Dissolve, Fade to Black,
  Wipe, Slide, Zoom, Circle (Iris), Blur, Glitch, Flash White.
- **Motion** — har image par keyframe jaisi movement: Zoom In/Out,
  Pan Left/Right/Up/Down. (Video clips par motion nahi lagta.)
- **Effect** — poori video par: Film Grain, VHS Retro, RGB Split,
  Camera Shake, Blur Focus-in.
- **Captions** — lafzon ke exact waqt par subtitles: Karaoke (bola hua lafz
  highlight), Standard, ya Pop. Font size, text/highlight rang, position
  (top/center/bottom) aur background box customize kar sakte hain.

## Notes

- Pehli transcription mein voice model download hota hai (~150MB, ek baar).
- Lambi audio (10+ minute) par transcription mein kuch minute lag sakte hain —
  be-fikr rahein, progress nazar aayegi.
- Upload ki hui files `data/` folder mein rehti hain; chaahein to waqtan fo waqtan
  saaf kar dein.
- Port badalna ho to: `PORT=8080 python app.py`
