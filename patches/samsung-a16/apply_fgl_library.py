#!/usr/bin/env python3
"""Add Nova FGL's local-library scan and root-scoped auto-scrape flow.

FGL has Android's all-files access, so its library root is a normal local
file path.  This deliberately keeps SAF/document-tree handling out of the
library feature.
"""
from pathlib import Path


def replace_once(source: str, old: str, new: str, description: str) -> str:
    if new in source:
        return source
    if old not in source:
        raise SystemExit(f"Unexpected upstream source while applying {description}")
    return source.replace(old, new, 1)


video_root = Path("Video")
media_root = Path("MediaLib")
folder_browser = video_root / "src/main/java/com/archos/mediacenter/video/browser/filebrowsing/BrowserByFolder.java"
main_activity = video_root / "src/main/java/com/archos/mediacenter/video/browser/MainActivity.java"
strings = video_root / "res/values/strings.xml"
manager = video_root / "src/main/java/com/archos/mediacenter/video/fgl/FglLibraryManager.java"
auto_scraper = media_root / "src/com/archos/mediascraper/AutoScrapeService.java"

for path in (folder_browser, main_activity, strings, auto_scraper):
    if not path.is_file():
        raise SystemExit(f"Missing {path}: sync Nova component sources before applying the FGL library patch")

manager.parent.mkdir(parents=True, exist_ok=True)
manager.write_text('''// Copyright 2026 Nova FGL
package com.archos.mediacenter.video.fgl;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;

import androidx.preference.PreferenceManager;

import com.archos.mediascraper.AutoScrapeService;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * FGL's small library boundary: a local root, bulk MediaStore registration and
 * Nova's existing scraper limited to that root.  It intentionally does not
 * use a SAF tree URI.
 */
public final class FglLibraryManager {
    private static final String PREF_ROOT = "fgl_library_root";
    private static final String PREF_LAST_SCAN = "fgl_library_last_scan";
    private static final long RESCAN_INTERVAL_MS = 15 * 60 * 1000L;
    private static final long SCRAPE_SETTLE_DELAY_MS = 6000L;
    private static final long SCRAPE_RETRY_DELAY_MS = 25000L;
    private static final AtomicBoolean sScanRunning = new AtomicBoolean(false);
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final Set<String> VIDEO_EXTENSIONS = new HashSet<>(Arrays.asList(
            "3gp", "3g2", "asf", "avi", "divx", "flv", "m2ts", "m4v", "mkv",
            "mov", "mp4", "mpeg", "mpg", "mts", "ogm", "ogv", "rm", "rmvb",
            "ts", "vob", "webm", "wmv"));

    private FglLibraryManager() { }

    public static boolean isEligibleRoot(Uri uri) {
        return uri != null && "file".equals(uri.getScheme());
    }

    public static void setLibraryRoot(Context context, Uri uri) {
        if (!isEligibleRoot(uri)) return;
        File root = new File(uri.getPath());
        if (!root.isDirectory()) return;
        try {
            root = root.getCanonicalFile();
        } catch (IOException ignored) { }
        SharedPreferences prefs = PreferenceManager.getDefaultSharedPreferences(context);
        prefs.edit().putString(PREF_ROOT, root.getPath()).putLong(PREF_LAST_SCAN, 0L).apply();
        scanRoot(context.getApplicationContext(), root, true);
    }

    /** Called when Nova returns to foreground: new episodes are picked up without a SAF prompt. */
    public static void rescanConfiguredRoot(Context context) {
        SharedPreferences prefs = PreferenceManager.getDefaultSharedPreferences(context);
        String rootPath = prefs.getString(PREF_ROOT, null);
        if (rootPath == null || rootPath.isEmpty()) return;
        if (System.currentTimeMillis() - prefs.getLong(PREF_LAST_SCAN, 0L) < RESCAN_INTERVAL_MS) return;
        scanRoot(context.getApplicationContext(), new File(rootPath), false);
    }

    private static void scanRoot(Context context, File root, boolean explicit) {
        if (!root.isDirectory() || !sScanRunning.compareAndSet(false, true)) return;
        new Thread(() -> {
            try {
                ArrayList<String> videoPaths = new ArrayList<>();
                collectVideoPaths(root, videoPaths);
                PreferenceManager.getDefaultSharedPreferences(context)
                        .edit().putLong(PREF_LAST_SCAN, System.currentTimeMillis()).apply();
                if (videoPaths.isEmpty()) return;
                MediaScannerConnection.scanFile(context,
                        videoPaths.toArray(new String[0]), null,
                        (path, scannedUri) -> { });
                // The system scanner inserts MediaStore rows first; Nova's importer follows.
                // Give that hand-off a short, bounded settling period before querying it.
                requestRootScrape(context, root.getPath(), SCRAPE_SETTLE_DELAY_MS);
                // Importing MediaStore rows is asynchronous. A bounded retry catches
                // slow removable storage without turning this into a polling loop.
                requestRootScrape(context, root.getPath(), SCRAPE_RETRY_DELAY_MS);
            } finally {
                sScanRunning.set(false);
            }
        }, explicit ? "FglLibraryInitialScan" : "FglLibraryRefresh").start();
    }

    private static void requestRootScrape(Context context, String rootPath, long delayMs) {
        MAIN.postDelayed(() -> AutoScrapeService.startServiceForRoot(context, rootPath), delayMs);
    }

    private static void collectVideoPaths(File directory, ArrayList<String> output) {
        File[] entries = directory.listFiles();
        if (entries == null) return;
        for (File entry : entries) {
            if (entry.isDirectory()) {
                collectVideoPaths(entry, output);
            } else if (entry.isFile() && isVideoFile(entry.getName())) {
                output.add(entry.getPath());
            }
        }
    }

    private static boolean isVideoFile(String name) {
        int dot = name.lastIndexOf('.');
        return dot >= 0 && dot < name.length() - 1
                && VIDEO_EXTENSIONS.contains(name.substring(dot + 1).toLowerCase(Locale.ROOT));
    }
}
''')

src = folder_browser.read_text()
src = replace_once(
    src,
    "import com.archos.mediacenter.video.CustomApplication;\n",
    "import com.archos.mediacenter.video.CustomApplication;\nimport com.archos.mediacenter.video.fgl.FglLibraryManager;\n",
    "FGL library manager import",
)
src = replace_once(
    src,
    "    private static final int DIALOG_LISTING = 4;\n",
    "    private static final int DIALOG_LISTING = 4;\n    private static final int MENU_SET_FGL_LIBRARY_ROOT = 0x46474C;\n",
    "FGL library menu id",
)
src = replace_once(
    src,
    '''        if (item instanceof MetaFile2) {
            menu.setHeaderTitle(((MetaFile2) item).getName());
            if (((MetaFile2) item).canWrite())
                menu.add(0, R.string.delete, 0, R.string.delete);
            return;
        }
        super.onCreateContextMenu(menu,v, menuInfo);
    }
''',
    '''        if (item instanceof MetaFile2) {
            MetaFile2 file = (MetaFile2) item;
            menu.setHeaderTitle(file.getName());
            if (file.isDirectory() && FglLibraryManager.isEligibleRoot(file.getUri()))
                menu.add(0, MENU_SET_FGL_LIBRARY_ROOT, 0, R.string.fgl_library_set_root);
            if (file.canWrite())
                menu.add(0, R.string.delete, 0, R.string.delete);
            return;
        }
        super.onCreateContextMenu(menu,v, menuInfo);
    }

    @Override
    public boolean onContextItemSelected(MenuItem item) {
        if (item.getItemId() == MENU_SET_FGL_LIBRARY_ROOT) {
            AdapterView.AdapterContextMenuInfo info = (AdapterView.AdapterContextMenuInfo) item.getMenuInfo();
            if (info == null) return true;
            Object selected = mFilesAdapter.getItem(info.position);
            if (selected instanceof MetaFile2) {
                FglLibraryManager.setLibraryRoot(getActivity(), ((MetaFile2) selected).getUri());
                Toast.makeText(getActivity(), R.string.fgl_library_scan_started, Toast.LENGTH_SHORT).show();
            }
            return true;
        }
        return super.onContextItemSelected(item);
    }
''',
    "FGL root folder action",
)
folder_browser.write_text(src)

src = main_activity.read_text()
src = replace_once(
    src,
    "import com.archos.mediacenter.video.R;\n",
    "import com.archos.mediacenter.video.R;\nimport com.archos.mediacenter.video.fgl.FglLibraryManager;\n",
    "FGL foreground refresh import",
)
src = replace_once(
    src,
    '''        mPermissionChecker.checkAndRequestPermission(this);

        // Reload preferences that may have changed
''',
    '''        mPermissionChecker.checkAndRequestPermission(this);
        FglLibraryManager.rescanConfiguredRoot(this);

        // Reload preferences that may have changed
''',
    "FGL foreground refresh",
)
main_activity.write_text(src)

src = strings.read_text()
src = replace_once(
    src,
    "    <string name=\"workgroup\">Workgroup</string>\n",
    "    <string name=\"workgroup\">Workgroup</string>\n    <string name=\"fgl_library_set_root\">Use as Nova FGL library</string>\n    <string name=\"fgl_library_scan_started\">Nova FGL is indexing this library</string>\n",
    "FGL library labels",
)
strings.write_text(src)

src = auto_scraper.read_text()
src = replace_once(
    src,
    "    public static final String RESCAN_ONLY_DESC_NOT_FOUND = \"rescan_only_desc_not_found\";\n",
    "    public static final String RESCAN_ONLY_DESC_NOT_FOUND = \"rescan_only_desc_not_found\";\n    public static final String EXTRA_ROOT_PATH = \"autoscrape_root_path\";\n",
    "root scrape intent extra",
)
src = replace_once(
    src,
    "    private boolean restartOnNextRound = false;\n    private AutoScraperBinder mBinder;\n",
    "    private boolean restartOnNextRound = false;\n    private volatile String mRequestedRootPath;\n    private final ThreadLocal<String> mScrapeRootPath = new ThreadLocal<>();\n    private AutoScraperBinder mBinder;\n",
    "root scrape state",
)
src = replace_once(
    src,
    '''    public static void startService(Context context) {
        if (log.isDebugEnabled()) log.debug("startService in foreground");
        mContext = context.getApplicationContext();
        try {
            context.startService(new Intent(context, AutoScrapeService.class));
        } catch (Exception e) {
            clearPostScanPendingIfNoWorker(context);
            log.warn("startService: Failed to start AutoScrapeService - timing or background restriction issue", e);
        }
    }
''',
    '''    public static void startService(Context context) {
        if (log.isDebugEnabled()) log.debug("startService in foreground");
        mContext = context.getApplicationContext();
        try {
            context.startService(new Intent(context, AutoScrapeService.class));
        } catch (Exception e) {
            clearPostScanPendingIfNoWorker(context);
            log.warn("startService: Failed to start AutoScrapeService - timing or background restriction issue", e);
        }
    }

    /** Start the existing scraper, but only for a local library subtree. */
    public static void startServiceForRoot(Context context, String rootPath) {
        if (rootPath == null || rootPath.isEmpty()) return;
        mContext = context.getApplicationContext();
        Intent intent = new Intent(context, AutoScrapeService.class);
        intent.putExtra(EXTRA_ROOT_PATH, rootPath);
        try {
            context.startService(intent);
        } catch (Exception e) {
            clearPostScanPendingIfNoWorker(context);
            log.warn("startServiceForRoot: unable to start scraper for {}", rootPath, e);
        }
    }
''',
    "root scrape launcher",
)
src = replace_once(
    src,
    '''            isForeground = true;
            isForceAfterNetworkScan = intent != null && intent.getBooleanExtra("FORCE_AFTER_NETWORK_SCAN", false);
''',
    '''            isForeground = true;
            mRequestedRootPath = intent == null ? null : intent.getStringExtra(EXTRA_ROOT_PATH);
            isForceAfterNetworkScan = intent != null && intent.getBooleanExtra("FORCE_AFTER_NETWORK_SCAN", false);
''',
    "root scrape request state",
)
src = replace_once(
    src,
    '''        if (onlyNotFound) sRescanOnlyNotFound = true;

        Thread worker = new Thread() {
''',
    '''        if (onlyNotFound) sRescanOnlyNotFound = true;
        final String rootPath = mRequestedRootPath;

        Thread worker = new Thread() {
''',
    "root scrape worker capture",
)
src = replace_once(
    src,
    '''                public void run() {
                    Cursor cursor = null;
                    try {
                        if (log.isDebugEnabled()) log.debug("startScraping: thread starting");
''',
    '''                public void run() {
                    Cursor cursor = null;
                    try {
                        mScrapeRootPath.set(rootPath);
                        if (log.isDebugEnabled()) log.debug("startScraping: thread starting");
''',
    "root scrape worker setup",
)
src = replace_once(
    src,
    '''                        } finally {
                            releaseScrapeWorker(current);
''',
    '''                        } finally {
                            mScrapeRootPath.remove();
                            releaseScrapeWorker(current);
''',
    "root scrape worker cleanup",
)
src = replace_once(
    src,
    '''    private Cursor getFileListCursor(int scrapStatusParam, String sortOrder, Integer offset, Integer limit) {
        // Look for all the videos not yet processed and not located in the Camera folder
        final String cameraPath =  Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM).getPath() + "/Camera";
        String[] selectionArgs = new String[]{ cameraPath + "/%" };
        String where = getWhereClause(scrapStatusParam);
        final String LIMIT = ((offset != null) ? offset + ",": "") + ((limit != null) ? limit : "");
        if (limit != null || offset != null) {
            return getContentResolver().query(VideoStore.Video.Media.EXTERNAL_CONTENT_URI.buildUpon().appendQueryParameter("limit", LIMIT).build(), SCRAPER_ACTIVITY_COLS, where, selectionArgs, sortOrder);
        } else {
            return getContentResolver().query(VideoStore.Video.Media.EXTERNAL_CONTENT_URI, SCRAPER_ACTIVITY_COLS, where, selectionArgs, sortOrder);
        }
    }

    private Cursor getFileListCursorAfterId(int scrapStatusParam, long afterId, int limit) {
        final String cameraPath = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM).getPath() + "/Camera";
        String where = getWhereClause(scrapStatusParam) + " AND " + BaseColumns._ID + ">?";
        String[] selectionArgs = new String[]{ cameraPath + "/%", String.valueOf(afterId) };
        return getContentResolver().query(
                VideoStore.Video.Media.EXTERNAL_CONTENT_URI.buildUpon().appendQueryParameter("limit", String.valueOf(limit)).build(),
                SCRAPER_ACTIVITY_COLS, where, selectionArgs, BaseColumns._ID);
    }

    private long getNextIdAfter(int scrapStatusParam, long afterId) {
        final String cameraPath = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM).getPath() + "/Camera";
        String where = getWhereClause(scrapStatusParam) + " AND " + BaseColumns._ID + ">?";
        String[] selectionArgs = new String[]{ cameraPath + "/%", String.valueOf(afterId) };
        String[] idOnly = new String[]{ BaseColumns._ID };
        Cursor c = getContentResolver().query(
                VideoStore.Video.Media.EXTERNAL_CONTENT_URI.buildUpon().appendQueryParameter("limit", "1").build(),
                idOnly, where, selectionArgs, BaseColumns._ID);
''',
    '''    private String appendRootFilter(String where) {
        String rootPath = mScrapeRootPath.get();
        return rootPath == null || rootPath.isEmpty() ? where
                : where + " AND " + VideoStore.MediaColumns.DATA + " LIKE ?";
    }

    private String[] selectionArgsForRoot(String cameraPath, String afterId) {
        String rootPath = mScrapeRootPath.get();
        if (rootPath == null || rootPath.isEmpty()) {
            return afterId == null ? new String[]{ cameraPath + "/%" }
                    : new String[]{ cameraPath + "/%", afterId };
        }
        return afterId == null ? new String[]{ cameraPath + "/%", rootPath + "/%" }
                : new String[]{ cameraPath + "/%", rootPath + "/%", afterId };
    }

    private Cursor getFileListCursor(int scrapStatusParam, String sortOrder, Integer offset, Integer limit) {
        final String cameraPath =  Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM).getPath() + "/Camera";
        String where = appendRootFilter(getWhereClause(scrapStatusParam));
        String[] selectionArgs = selectionArgsForRoot(cameraPath, null);
        final String LIMIT = ((offset != null) ? offset + ",": "") + ((limit != null) ? limit : "");
        if (limit != null || offset != null) {
            return getContentResolver().query(VideoStore.Video.Media.EXTERNAL_CONTENT_URI.buildUpon().appendQueryParameter("limit", LIMIT).build(), SCRAPER_ACTIVITY_COLS, where, selectionArgs, sortOrder);
        } else {
            return getContentResolver().query(VideoStore.Video.Media.EXTERNAL_CONTENT_URI, SCRAPER_ACTIVITY_COLS, where, selectionArgs, sortOrder);
        }
    }

    private Cursor getFileListCursorAfterId(int scrapStatusParam, long afterId, int limit) {
        final String cameraPath = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM).getPath() + "/Camera";
        String where = appendRootFilter(getWhereClause(scrapStatusParam)) + " AND " + BaseColumns._ID + ">?";
        String[] selectionArgs = selectionArgsForRoot(cameraPath, String.valueOf(afterId));
        return getContentResolver().query(
                VideoStore.Video.Media.EXTERNAL_CONTENT_URI.buildUpon().appendQueryParameter("limit", String.valueOf(limit)).build(),
                SCRAPER_ACTIVITY_COLS, where, selectionArgs, BaseColumns._ID);
    }

    private long getNextIdAfter(int scrapStatusParam, long afterId) {
        final String cameraPath = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM).getPath() + "/Camera";
        String where = appendRootFilter(getWhereClause(scrapStatusParam)) + " AND " + BaseColumns._ID + ">?";
        String[] selectionArgs = selectionArgsForRoot(cameraPath, String.valueOf(afterId));
        String[] idOnly = new String[]{ BaseColumns._ID };
        Cursor c = getContentResolver().query(
                VideoStore.Video.Media.EXTERNAL_CONTENT_URI.buildUpon().appendQueryParameter("limit", "1").build(),
                idOnly, where, selectionArgs, BaseColumns._ID);
''',
    "root-filtered scraper queries",
)
auto_scraper.write_text(src)
print("Patched FGL library root, bulk scan and root-scoped auto-scraper")
