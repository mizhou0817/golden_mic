# Bundled subtitle font

- Family: Noto Sans SC
- Files: `NotoSansSC-Regular.ttf` (wght 400), `NotoSansSC-Bold.ttf` (wght 700)
- Source: https://github.com/google/fonts/tree/main/ofl/notosanssc, file `NotoSansSC[wght].ttf`
  (retrieved 2026-07-28, SHA-256 `A3041811A78C361B1DE50F953C805E0244951C21C5BD412F7232EF0D899AF0DA`)
- Made with fontTools 4.66.1 `instancer` (`instantiateVariableFont(font, {"wght": 400|700}, updateFontNames=True)`).
  Static instances are required: libass (FFmpeg's subtitle renderer) cannot select the variable font, so on a
  server without system Chinese fonts every character rendered as a box. macOS hid this by falling back to PingFang.
- SHA-256: Regular `533F8FA55F77F828F999B6057523A4BD55A68704CE0D610F60C5EB6BD77A431B`, Bold `38B46719ADE8C194FAD5F2B7CCCF9AABE62930F8F7181353CC5FF1F79958F663`
- License: SIL Open Font License 1.1; see `OFL.txt`
- License SHA-256: `1C05C68C34F9708415AADA51F17E1B0092D2CEA709BF4A94CD38114F9E73D7D9`

The filenames intentionally start with `NotoSansSC` because production subtitle validation only accepts explicitly bundled Noto Sans SC font assets.
