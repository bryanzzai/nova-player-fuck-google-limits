#!/usr/bin/env python3
"""Label sideload-only APK Nova FGL; keep upstream app ID for first compatibility build."""
from pathlib import Path
p=Path("Video/src/manageexternalstorage/AndroidManifest.xml")
s=p.read_text()
old='android:label="@string/nova"'
assert old in s, "Upstream manifest changed"
p.write_text(s.replace(old,'android:label="Nova FGL"',1))
print("Set visible application label to Nova FGL")
