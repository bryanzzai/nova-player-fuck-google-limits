# Samsung A16 USB access patch — first implementation

This directory contains an **actual source-code patch script**, not an APK.
After syncing the original Nova multi-repository workspace so that `Video/` exists,
run:

```sh
python3 AVP/patches/samsung-a16/apply_usb_saf_fix.py
```

(or run the script with the path adjusted for your checkout).

## What the first patch changes
- In `BrowserByExtStorage.java`, request persistent SAF permissions **only for flags actually returned** by Android, instead of always insisting on write access.
- Detect an invalid/null selection or missing read grant.
- Catch a rejected persistent permission cleanly rather than throwing an uncaught `SecurityException`.
- Update the currently selected mount URI and reset the ask guard after a successful grant.
- Leave SMB, networking, indexing, metadata and playback untouched.

## Important limitations
- **Not tested** on Samsung A16 or compiled yet.
- A second prompt may still arise from incorrect URI construction or fragment lifecycle; this patch fixes one plausible trigger, not every known path.
- The component source lives in upstream `aos-Video`, not directly in the manifest fork. This overlay script makes the precise change reproducible without writing to anyone else's repositories.
- The next task is to verify the produced source diff and build a debug APK; do not call this a confirmed fix.

## Regression checks
1. Choose a USB subfolder via Android's system picker and verify that the video list appears.
2. Leave Nova, reopen, and check that the grant persists.
3. Disconnect and reconnect the USB volume.
4. Verify SMB browsing still works.
