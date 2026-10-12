#!/usr/bin/env python3
"""Use Nova's existing manageexternalstorage flavor and open special-access Settings directly."""
from pathlib import Path

p = Path("Video/src/main/java/com/archos/mediacenter/video/browser/PermissionChecker.java")
s = p.read_text()
start = s.index('            if (!isDialogDisplayed && !Environment.isExternalStorageManager()) {')
end = s.index('\n        } else {', start)
# Ends the manage-storage branch, not the nested version-specific condition.
old = s[start:end]
new = '''            if (!isDialogDisplayed && !Environment.isExternalStorageManager()) {
                // MANAGE_EXTERNAL_STORAGE is a special app access, NOT a runtime
                // permission. requestPermissions() cannot grant it on Android 13+.
                // Open Samsung's All files access screen directly, once.
                isDialogDisplayed = true;
                try {
                    Intent grantIntent = new Intent(
                            Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:" + mActivity.getPackageName()));
                    mActivity.startActivity(grantIntent);
                } catch (ActivityNotFoundException | SecurityException ex) {
                    try {
                        mActivity.startActivity(new Intent(
                                Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                    } catch (ActivityNotFoundException | SecurityException ignored) {
                        isDialogDisplayed = false;
                        log.warn("No all-files access settings activity available", ignored);
                    }
                }
            }'''
s = s[:start] + new + s[end:]
p.write_text(s)
print("Patched special-access flow:", p)
