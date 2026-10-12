# Samsung A16 USB storage compatibility investigation

## Reproduced user-reported behavior
- Nova Video Player 6.4.72 works on an older Huawei P Smart 2019 with the same USB video collection.
- On Samsung A16 (newer Android), Nova asks for access to USB storage repeatedly, even after the user grants access; the library stays loading.
- Huawei displays a broad **Storage** permission; Samsung displays **Photos and videos**.
- The USB library is about 1 TB; the failure is a repeated permission prompt, not just a slow scan.

## Confirmed source layout
This repository (`aos-AVP`) is the build/manifest entry point, **not** the main Android app source. `default.xml` assembles separate upstream projects:
- `nova-video-player/aos-Video` — Android player UI / app code.
- `nova-video-player/aos-MediaLib` — media library indexing.
- `nova-video-player/aos-FileCoreLibrary` — filesystem access.

`.gitmodules` likewise points at these external repositories. Do not attempt to fix the Android permission loop merely by editing `aos-AVP`'s top-level manifest, and do not assume a submodule pointer represents code owned by this fork.

## Planned investigation
1. Inspect `aos-Video` Android manifest, target SDK and storage permissions, plus code handling USB volume selection and Android Storage Access Framework (SAF) tree-picker results.
2. Locate any repeated-grant condition: persisted URI permissions, URI-to-volume comparisons, and MediaStore USB scanning.
3. Add diagnostic logging and a targeted, minimal correction for the failure; avoid requesting all-files access unless supported by the actual code path.
4. Preserve a separate application ID and signing configuration for a side-by-side debug build; only after validating dependency and application-ID interactions.
5. Build and test on Samsung A16 before treating the issue as resolved.

## Acceptance criteria
- USB permission survives relaunch and does not trigger the same picker again.
- Video list appears from a known-small USB test folder, then the full library.
- External subtitles can be read, with agreed matching rules for `Subs` / `Subtitles`.
- Existing stable Nova installation is not overwritten.
- No edits to unrelated repositories.

## Current state
**Investigation initialized; no Android source change or APK build yet.** Actual code changes belong in the source component repositories and must be connected deliberately to the build manifest.
