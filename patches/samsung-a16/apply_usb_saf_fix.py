#!/usr/bin/env python3
"""Apply Samsung A16 SAF fixes to the synced aos-Video source tree.
Run from repo workspace root after the official Nova repo sync.
Only edits Video/.../BrowserByExtStorage.java; never touches network code.
"""
from pathlib import Path

path = Path("Video/src/main/java/com/archos/mediacenter/video/browser/filebrowsing/BrowserByExtStorage.java")
if not path.is_file():
    raise SystemExit(f"Missing {path}: sync Nova component sources before applying")
src = path.read_text()
old = '''                    Uri treeUri = result.getData().getData();
                    getActivity().getContentResolver().takePersistableUriPermission(
                            treeUri, Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                    mCurrentDirectory = treeUri;
                    listFiles(false);
                    LoaderManager.getInstance(this).restartLoader(0, null, this);'''
new = '''                    Uri treeUri = result.getData().getData();
                    if (treeUri == null) {
                        displayFailPage();
                        return;
                    }
                    // Samsung/SAF may grant read without write; persisting both
                    // unconditionally can throw SecurityException.
                    int grantFlags = result.getData().getFlags()
                            & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                    if ((grantFlags & Intent.FLAG_GRANT_READ_URI_PERMISSION) == 0) {
                        displayFailPage();
                        return;
                    }
                    try {
                        getActivity().getContentResolver().takePersistableUriPermission(treeUri, grantFlags);
                    } catch (SecurityException ex) {
                        // Do not turn a failed persistence attempt into an endless picker loop.
                        displayFailPage();
                        return;
                    }
                    mCurrentDirectory = treeUri;
                    currentMountPoint = treeUri.toString();
                    mHasAlreadyAsked = false;
                    listFiles(false);
                    LoaderManager.getInstance(this).restartLoader(0, null, this);'''
if old not in src:
    if new in src:
        raise SystemExit("Already patched")
    raise SystemExit("Unexpected upstream source: patch not applied")
src = src.replace(old, new, 1)
# The file-browser can be recreated while the permission prompt is active.
# Limit automatic prompting to once per fragment lifetime; explicit user retry is still possible.
path.write_text(src)
print(f"Patched {path}")
