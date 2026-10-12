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
import_impl = media_root / "src/com/archos/mediaprovider/video/VideoStoreImportImpl.java"

for path in (folder_browser, main_activity, strings, auto_scraper, import_impl):
    if not path.is_file():
        raise SystemExit(f"Missing {path}: sync Nova component sources before applying the FGL library patch")

manager.parent.mkdir(parents=True, exist_ok=True)
manager.write_text('''// Copyright 2026 Nova FGL
package com.archos.mediacenter.video.fgl;

import android.app.Activity;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Handler;
import android.view.KeyEvent;
import android.os.Looper;
import android.provider.BaseColumns;
import android.view.WindowManager;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.preference.PreferenceManager;

import com.archos.mediacenter.video.R;
import com.archos.mediacenter.video.ui.NovaProgressDialog;
import com.archos.mediaprovider.ArchosMediaIntent;
import com.archos.mediaprovider.video.VideoStore;
import com.archos.mediaprovider.video.VideoStoreImportService;
import com.archos.mediaprovider.video.VideoStoreImportImpl;
import com.archos.mediascraper.AutoScrapeService;

import java.io.File;
import java.io.IOException;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * FGL's small library boundary: one local root, recursive registration in
 * MediaStore, Nova database import and then Nova's existing root-scoped
 * scraper. It intentionally does not use a SAF tree URI.
 */
public final class FglLibraryManager {
    private static final String PREF_ROOT = "fgl_library_root";
    private static final String PREF_LAST_SCAN = "fgl_library_last_scan";
    private static final long RESCAN_INTERVAL_MS = 15 * 60 * 1000L;
    private static final int SCAN_BATCH_SIZE = 50;
    // On a removable card, Android's MediaStore and Nova's import database can
    // take several minutes to settle after the file scanner has finished.
    // Keep reporting that distinct phase instead of treating 90 seconds as a
    // silent failure.
    private static final int IMPORT_WAIT_ATTEMPTS = 15 * 60;
    private static final long IMPORT_WAIT_DELAY_MS = 1000L;
    private static final AtomicBoolean sScanRunning = new AtomicBoolean(false);
    private static final AtomicBoolean sCancelRequested = new AtomicBoolean(false);
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
        scanRoot(context.getApplicationContext(), root, true, ProgressReporter.create(context));
    }

    /** Called when Nova returns to foreground: new episodes are picked up without a SAF prompt. */
    public static void rescanConfiguredRoot(Context context) {
        SharedPreferences prefs = PreferenceManager.getDefaultSharedPreferences(context);
        String rootPath = prefs.getString(PREF_ROOT, null);
        if (rootPath == null || rootPath.isEmpty()) return;
        if (System.currentTimeMillis() - prefs.getLong(PREF_LAST_SCAN, 0L) < RESCAN_INTERVAL_MS) return;
        scanRoot(context.getApplicationContext(), new File(rootPath), false, ProgressReporter.NONE);
    }

    private static void scanRoot(Context context, File root, boolean explicit, ProgressReporter progress) {
        if (!root.isDirectory() || !sScanRunning.compareAndSet(false, true)) return;
        sCancelRequested.set(false);
        progress.preparing();
        new Thread(() -> {
            ArrayList<String> videoPaths = new ArrayList<>();
            collectVideoPaths(root, videoPaths);
            if (finishIfCancelled(progress)) return;
            if (videoPaths.isEmpty()) {
                sScanRunning.set(false);
                progress.finished(context.getString(R.string.fgl_library_no_videos));
                return;
            }
            progress.filesFound(videoPaths.size());
            scanBatch(context, root, videoPaths, 0, new AtomicInteger(), progress,
                    explicit ? "FglLibraryInitialScan" : "FglLibraryRefresh");
        }, explicit ? "FglLibraryInitialScan" : "FglLibraryRefresh").start();
    }

    private static void scanBatch(Context context, File root, ArrayList<String> paths, int offset,
            AtomicInteger scannedCount, ProgressReporter progress, String workerName) {
        if (finishIfCancelled(progress)) return;
        if (offset >= paths.size()) {
            onMediaScanComplete(context, root, paths.size(), progress, workerName);
            return;
        }
        int end = Math.min(offset + SCAN_BATCH_SIZE, paths.size());
        String[] batch = paths.subList(offset, end).toArray(new String[0]);
        AtomicInteger batchCount = new AtomicInteger();
        MediaScannerConnection.scanFile(context, batch, null, (path, scannedUri) -> {
            if (finishIfCancelled(progress)) return;
            int scanned = scannedCount.incrementAndGet();
            progress.scanning(scanned, paths.size());
            if (batchCount.incrementAndGet() == batch.length) {
                scanBatch(context, root, paths, end, scannedCount, progress, workerName);
            }
        });
    }

    private static void onMediaScanComplete(Context context, File root, int expectedVideos,
            ProgressReporter progress, String workerName) {
        if (finishIfCancelled(progress)) return;
        PreferenceManager.getDefaultSharedPreferences(context)
                .edit().putLong(PREF_LAST_SCAN, System.currentTimeMillis()).apply();
        progress.importing(0, expectedVideos, 0);
        // A full Nova import walks every item in the device-wide MediaStore
        // before it publishes any of this library's rows.  On a large removable
        // card that leaves this dialog at “0” for minutes.  Import only the
        // already-scanned FGL root through Nova's own files_import table.
        new Thread(() -> {
            try {
                VideoStoreImportImpl importer = new VideoStoreImportImpl(context);
                try {
                    importer.importFglLibraryRoot(root.getPath());
                } finally {
                    importer.destroy();
                }
            } catch (Exception e) {
                sScanRunning.set(false);
                progress.finished(context.getString(R.string.fgl_library_import_failed, 0, expectedVideos));
                return;
            }
            waitForImport(context, root, expectedVideos, progress);
        }, workerName + "RootImport").start();
    }

    private static void waitForImport(Context context, File root, int expectedVideos,
            ProgressReporter progress) {
        int indexed = 0;
        for (int attempt = 0; attempt < IMPORT_WAIT_ATTEMPTS; attempt++) {
            if (finishIfCancelled(progress)) return;
            try {
                Thread.sleep(IMPORT_WAIT_DELAY_MS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
            indexed = countIndexedVideos(context, root.getPath());
            progress.importing(indexed, expectedVideos, attempt + 1);
            if (indexed >= expectedVideos) {
                AutoScrapeService.startServiceForRoot(context, root.getPath());
                sScanRunning.set(false);
                progress.finished(context.getString(R.string.fgl_library_scraping, indexed));
                return;
            }
        }
        sScanRunning.set(false);
        progress.finished(context.getString(R.string.fgl_library_import_failed, indexed, expectedVideos));
    }

    private static int countIndexedVideos(Context context, String rootPath) {
        Cursor cursor = null;
        try {
            cursor = context.getContentResolver().query(
                    VideoStore.Video.Media.EXTERNAL_CONTENT_URI,
                    new String[]{ BaseColumns._ID },
                    VideoStore.MediaColumns.DATA + " LIKE ?", new String[]{ rootPath + "/%" }, null);
            return cursor == null ? 0 : cursor.getCount();
        } catch (Exception ignored) {
            return 0;
        } finally {
            if (cursor != null) cursor.close();
        }
    }

    private static void collectVideoPaths(File directory, ArrayList<String> output) {
        if (sCancelRequested.get()) return;
        File[] entries = directory.listFiles();
        if (entries == null) return;
        for (File entry : entries) {
            if (sCancelRequested.get()) return;
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

    /** The back arrow is the only user gesture that may interrupt this job. */
    private static void cancelCurrentScan() {
        sCancelRequested.set(true);
    }

    private static boolean finishIfCancelled(ProgressReporter progress) {
        if (!sCancelRequested.get()) return false;
        if (sScanRunning.getAndSet(false)) progress.cancelled();
        return true;
    }

    private static final class ProgressReporter {
        static final ProgressReporter NONE = new ProgressReporter(null, null);

        private final Context context;
        private final WeakReference<Activity> activity;
        private NovaProgressDialog dialog;
        private OnBackPressedCallback backCallback;

        private ProgressReporter(Context context, Activity activity) {
            this.context = context;
            this.activity = new WeakReference<>(activity);
        }

        static ProgressReporter create(Context context) {
            return context instanceof Activity ? new ProgressReporter(context, (Activity) context) : NONE;
        }

        void preparing() {
            if (context == null) return;
            MAIN.post(() -> {
                Activity owner = activity.get();
                if (owner == null || owner.isFinishing()) return;
                dialog = new NovaProgressDialog(owner);
                dialog.setTitle(R.string.fgl_library_dialog_title);
                dialog.setMessage(context.getString(R.string.fgl_library_preparing));
                dialog.setProgressStyle(NovaProgressDialog.STYLE_HORIZONTAL);
                dialog.setIndeterminate(true);
                // Only Android's actual back action may cancel this job.
                // Outside taps remain disabled, but setCancelable(true) is
                // required for Dialog to route the system back arrow through
                // OnCancelListener on Samsung's Android build.
                dialog.setCancelable(true);
                dialog.setCanceledOnTouchOutside(false);
                dialog.setOnCancelListener(ignored -> cancelByBackArrow());
                owner.getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                dialog.show();
            });
        }

        private void installBackInterceptor(Activity owner) {
            removeBackInterceptor();
            if (!(owner instanceof ComponentActivity)) return;
            backCallback = new OnBackPressedCallback(true) {
                @Override
                public void handleOnBackPressed() {
                    cancelByBackArrow();
                }
            };
            ((ComponentActivity) owner).getOnBackPressedDispatcher()
                    .addCallback(backCallback);
        }

        private void removeBackInterceptor() {
            if (backCallback != null) {
                backCallback.remove();
                backCallback = null;
            }
        }

        private void cancelByBackArrow() {
            cancelCurrentScan();
            removeBackInterceptor();
            Activity owner = activity.get();
            if (owner != null && !owner.isFinishing())
                owner.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            if (dialog != null && dialog.isShowing()) dialog.dismiss();
        }

        void filesFound(int total) {
            if (context == null) return;
            MAIN.post(() -> {
                if (dialog == null || !dialog.isShowing()) return;
                dialog.setIndeterminate(false);
                dialog.setMax(total);
                dialog.setProgress(0);
                dialog.setMessage(context.getString(R.string.fgl_library_indexing, 0, total));
            });
        }

        void scanning(int current, int total) {
            if (context == null) return;
            MAIN.post(() -> {
                if (dialog == null || !dialog.isShowing()) return;
                dialog.setProgress(current);
                dialog.setMessage(context.getString(R.string.fgl_library_indexing, current, total));
            });
        }

        void importing(int current, int total, int elapsedSeconds) {
            if (context == null) return;
            MAIN.post(() -> {
                if (dialog == null || !dialog.isShowing()) return;
                dialog.setMax(total);
                dialog.setProgress(Math.min(current, total));
                dialog.setMessage(context.getString(R.string.fgl_library_importing,
                        current, total, elapsedSeconds / 60, elapsedSeconds % 60));
            });
        }

        void finished(String message) {
            if (context == null) return;
            MAIN.post(() -> {
                removeBackInterceptor();
                if (dialog == null || !dialog.isShowing()) return;
                dialog.setMessage(message);
                Activity owner = activity.get();
                if (owner != null && !owner.isFinishing())
                    owner.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                dialog.setCancelable(true);
                dialog.setCanceledOnTouchOutside(false);
                dialog.setButton(DialogInterface.BUTTON_POSITIVE,
                        context.getString(android.R.string.ok),
                        (ignored, which) -> dialog.dismiss());
            });
        }

        void cancelled() {
            if (context == null) return;
            MAIN.post(() -> {
                removeBackInterceptor();
                Activity owner = activity.get();
                if (owner != null && !owner.isFinishing())
                    owner.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                if (dialog != null && dialog.isShowing()) dialog.dismiss();
            });
        }
    }
}
''')

src = import_impl.read_text()
src = replace_once(
    src,
    "    public void doFullImport() {\n",
    '''    /**
     * Import just one already MediaScanner-indexed local root.  This is the
     * FGL path: it avoids blocking the user behind a device-wide import before
     * the selected USB/SD library becomes visible.
     */
    public int importFglLibraryRoot(String rootPath) {
        if (TextUtils.isEmpty(rootPath)) return 0;
        mIsImportInterrupted = false;
        Cursor allFiles = null;
        Cursor existing = null;
        try {
            ensureSdCardPath();
            final boolean modern = Build.VERSION.SDK_INT > Build.VERSION_CODES.O;
            String selection = (modern ? WHERE_ALL_AP : WHERE_ALL_BP) + BLACKLIST
                    + " AND " + MediaColumnsDATA + " LIKE ?"
                    + " AND " + FileColumns.MEDIA_TYPE + "=?";
            String[] args = new String[]{rootPath + "/%",
                    String.valueOf(FileColumns.MEDIA_TYPE_VIDEO)};
            String[] projection = modern ? FILES_PROJECTION_AP : FILES_PROJECTION_BP;
            allFiles = CustomCursor.wrap(mCr.query(MediaStore.Files.getContentUri("external"),
                    projection, selection, args, BaseColumns._ID));
            if (allFiles == null) return 0;

            HashSet<Long> importedIds = new HashSet<>();
            existing = mCr.query(VideoStoreInternal.FILES_IMPORT,
                    new String[]{BaseColumns._ID}, null, null, null);
            if (existing != null) {
                while (existing.moveToNext() && !mIsImportInterrupted)
                    importedIds.add(existing.getLong(0));
            }

            BulkInserter inserter = new BulkInserter(VideoStoreInternal.FILES_IMPORT, mCr, 250);
            ExtStorageManager extStorageManager = ExtStorageManager.getExtStorageManager();
            int columnCount = allFiles.getColumnCount();
            while (allFiles.moveToNext() && !mIsImportInterrupted) {
                ContentValues values = new ContentValues(columnCount + 1);
                DatabaseUtils.cursorRowToContentValues(allFiles, values);
                Long id = values.getAsLong(BaseColumns._ID);
                if (id == null || importedIds.contains(id)) continue;
                String data = values.getAsString(MediaColumnsDATA);
                Integer storageId = data != null && data.startsWith(sdCardPath)
                        ? 1 : extStorageManager.getStorageId3(data);
                values.put("storage_id", storageId);
                inserter.add(values);
            }
            return inserter.execute();
        } finally {
            if (existing != null) existing.close();
            if (allFiles != null) allFiles.close();
        }
    }

    public void doFullImport() {
''',
    "FGL root-only MediaStore import",
)
import_impl.write_text(src)

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
    "    <string name=\"workgroup\">Workgroup</string>\n"
    "    <string name=\"fgl_library_set_root\">Use as Nova FGL library</string>\n"
    "    <string name=\"fgl_library_scan_started\">Nova FGL is indexing this library</string>\n"
    "    <string name=\"fgl_library_dialog_title\">Nova FGL library</string>\n"
    "    <string name=\"fgl_library_preparing\">Finding video files…</string>\n"
    "    <string name=\"fgl_library_indexing\">Indexing %1$d of %2$d video files</string>\n"
    "    <string name=\"fgl_library_importing\">Adding %1$d of %2$d videos to Nova (%3$d:%4$02d)</string>\n"
    "    <string name=\"fgl_library_scraping\">Indexed %1$d videos. Fetching metadata…</string>\n"
    "    <string name=\"fgl_library_no_videos\">No video files were found in this library</string>\n"
    "    <string name=\"fgl_library_import_failed\">Only %1$d of %2$d videos reached Nova’s library</string>\n",
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
