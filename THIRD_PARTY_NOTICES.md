# Third-party notices

## Fonts

`rr_archive/web/static/fonts/` contains unmodified `.woff2` subsets of the fonts below, used by the local dashboard. `rr_archive/web/static/static_catalog.html` inlines the same files as base64 `data:` URIs, so the exported catalogue renders correctly from `file://`. `tools/build_fonts.py` re-downloads them. Each font is distributed under the **SIL Open Font License, Version 1.1** (full text: <https://openfontlicense.org/open-font-license-official-text/>).

| Font | Copyright | Source |
|---|---|---|
| Oswald | Copyright 2016 The Oswald Project Authors | <https://github.com/googlefonts/OswaldFont> |
| PT Serif | Copyright © 2010 ParaType Ltd. (Reserved Font Names "PT Sans", "PT Serif", "PT Mono" and "ParaType") | <https://www.paratype.com/public> |
| PT Mono | Copyright © 2010 ParaType Ltd. (Reserved Font Names as above) | <https://www.paratype.com/public> |
| Inter | Copyright 2016 The Inter Project Authors | <https://github.com/rsms/inter> |
| JetBrains Mono | Copyright 2020 The JetBrains Mono Project Authors | <https://github.com/JetBrains/JetBrainsMono> |

## Software dependencies

The dependencies are installed from PyPI (see `requirements.txt`) and are not vendored in this repository. Each is under its own license. The FFmpeg binary used by the gift builder is downloaded by `imageio-ffmpeg` at install time and is subject to FFmpeg's license (LGPL/GPL, depending on the build).

## Archive content

No recordings, label scans, PDFs or metadata from russian-records.com are included in this repository.
