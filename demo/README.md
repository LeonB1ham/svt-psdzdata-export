# Demo

Sample SVT-Ist / SVT-Soll / TAL plus a tiny fake PSdZData. No real BMW firmware.

| File | What it is |
|---|---|
| `sample-svt.xml` | target SVT (Soll) |
| `sample-svt-ist.xml` | current SVT (Ist) with older BTLD, SWFL and CAFD |
| `sample-tal.xml` | TAL for the same change |
| `psdzdata/swe/` | placeholders: found, other version, `.bin` without `.xml` |

```bat
.\demo\run-demo.ps1
```

Output goes to `demo/out/`. Reference manifest: `export-example/svt_export_manifest.txt`.

GUI:

```bat
py -3 app.py --demo
```

Window screenshots (English UI, made with `py -3 demo\capture_screens.py`): `screenshots/01-single.png` … `05-batch.png`.
