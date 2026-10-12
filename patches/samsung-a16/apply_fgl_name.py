#!/usr/bin/env python3
"""Label sideload-only APK Nova FGL; keep upstream app ID for first compatibility build."""
from pathlib import Path

p = Path("Video/src/manageexternalstorage/AndroidManifest.xml")
s = p.read_text()
android_ns = 'xmlns:android="http://schemas.android.com/apk/res/android"'
tools_ns = 'xmlns:tools="http://schemas.android.com/tools"'
assert android_ns in s, "Upstream manifest changed"
if tools_ns not in s:
    s = s.replace(android_ns, f"{android_ns} {tools_ns}", 1)
old = 'android:label="@string/nova"'
new = 'android:label="Nova FGL" tools:replace="android:label"'
assert old in s, "Upstream label changed"
p.write_text(s.replace(old, new, 1))
print("Set visible application label to Nova FGL")
