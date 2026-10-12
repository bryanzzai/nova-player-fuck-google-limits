#!/usr/bin/env python3
"""Use Nova's existing manageexternalstorage flavor and open special-access Settings directly."""
from pathlib import Path

p = Path("Video/src/main/java/com/archos/mediacenter/video/browser/PermissionChecker.java")
s = p.read_text()
s = s.replace(
    "    public boolean isDialogDisplayed = false;\n",
    "    public boolean isDialogDisplayed = false;\n"
    "    // The special all-files Settings screen has no runtime-permission callback.\n"
    "    // Remember that FGL sent the user there, so returning with the switch on\n"
    "    // can trigger Nova's normal import path exactly once.\n"
    "    private boolean waitingForManageStorageGrant = false;\n",
    1,
)
start = s.index('            if (!isDialogDisplayed && !Environment.isExternalStorageManager()) {')
end = s.index('\n        } else {', start)
# Ends the manage-storage branch, not the nested version-specific condition.
old = s[start:end]
new = '''            if (Environment.isExternalStorageManager()) {
                if (waitingForManageStorageGrant) {
                    waitingForManageStorageGrant = false;
                    isDialogDisplayed = false;
                    // Settings returns through onResume(), not onRequestPermissionsResult().
                    // Start Nova's importer before a USB browser attempts its first listing.
                    launchScan();
                }
            } else if (!isDialogDisplayed) {
                // MANAGE_EXTERNAL_STORAGE is a special app access, NOT a runtime
                // permission. requestPermissions() cannot grant it on Android 13+.
                // Open Samsung's All files access screen directly, once.
                isDialogDisplayed = true;
                waitingForManageStorageGrant = true;
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
                        waitingForManageStorageGrant = false;
                        log.warn("No all-files access settings activity available", ignored);
                    }
                }
            }'''
s = s[:start] + new + s[end:]
p.write_text(s)
print("Patched special-access flow:", p)
