# All-files-access build for Samsung A16

## Concrete source finding
Nova already ships a `manageexternalstorage` Gradle flavor (`Video/build.gradle`) and a flavor-specific manifest (`Video/src/manageexternalstorage/AndroidManifest.xml`) containing `android.permission.MANAGE_EXTERNAL_STORAGE`.

The existing `PermissionChecker` incorrectly treats this **special app access** as a normal runtime permission first, potentially contributing to repeated permission prompts. For a sideload-only build, the patch opens the Android **All files access** settings directly and checks `Environment.isExternalStorageManager()`.

This change is **not** a way to grant access silently. The phone owner still needs to switch on **Allow access to manage all files** once in Samsung settings.

## Apply patches after Nova workspace sync

```sh
python3 AVP/patches/samsung-a16/apply_all_files_access.py
python3 AVP/patches/samsung-a16/apply_usb_saf_fix.py
```

Build the **manageexternalstorage** flavor (not `noamazon`) using the project's supported Gradle build process. Verify the final merged APK manifest includes `MANAGE_EXTERNAL_STORAGE`. Do not distribute it through Play Store.

## Scope
- Keep SMB, networking, metadata and video decode code untouched.
- Full access applies to shared storage areas exposed by Android. It cannot bypass Android's protected directories or mount/driver restrictions, and direct USB availability still depends on Samsung's mounting of the drive.
- First test permission persistence, USB browsing and playback on a small folder before indexing the full 1 TB library.
- This patch is not yet compiled or tested on-device.
