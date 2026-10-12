#!/usr/bin/env python3
"""Apply Nova FGL's all-files storage mode to the synced aos-Video source tree.

FGL relies on the user-approved MANAGE_EXTERNAL_STORAGE special access.  It
therefore deliberately does not use Nova's SAF tree-picker fallback.
"""
from pathlib import Path


def replace_once(source: str, old: str, new: str, description: str) -> str:
    if old not in source:
        if new in source:
            return source
        raise SystemExit(f"Unexpected upstream source while removing {description}")
    return source.replace(old, new, 1)


ext_storage = Path(
    "Video/src/main/java/com/archos/mediacenter/video/browser/"
    "filebrowsing/BrowserByExtStorage.java"
)
folder_browser = Path(
    "Video/src/main/java/com/archos/mediacenter/video/browser/"
    "filebrowsing/BrowserByFolder.java"
)
if not ext_storage.is_file() or not folder_browser.is_file():
    raise SystemExit("Sync Nova component sources before applying the FGL storage patch")

src = ext_storage.read_text()
for line in (
    "import android.annotation.TargetApi;\n",
    "import android.app.Activity;\n",
    "import android.content.DialogInterface;\n",
    "import android.os.Build;\n",
    "import androidx.activity.result.ActivityResultLauncher;\n",
    "import androidx.activity.result.contract.ActivityResultContracts;\n",
    "import androidx.appcompat.app.AlertDialog;\n",
    "import androidx.loader.app.LoaderManager;\n",
    "import com.archos.filecorelibrary.contentstorage.DocumentUriBuilder;\n",
    "import com.archos.mediacenter.filecoreextension.UriUtils;\n",
    "import com.archos.mediacenter.video.R;\n",
):
    src = src.replace(line, "")
if "import java.io.File;\n" not in src:
    src = replace_once(
        src,
        "import android.net.Uri;\n",
        "import android.net.Uri;\n\nimport java.io.File;\n",
        "local mount File import",
    )
start = src.find(
    "    private final ActivityResultLauncher<Intent> docTreeLauncher = "
    "registerForActivityResult("
)
if start != -1:
    end = src.find("\n\n    private final BroadcastReceiver", start)
    if end == -1:
        raise SystemExit("Unexpected upstream source while removing SAF result callback")
    src = src[:start] + src[end + 2:]
src = src.replace("    private static final int READ_REQUEST_CODE = 42;\n", "")
src = src.replace("    private boolean mHasAlreadyAsked = false;\n", "")
src = src.replace(
    "    private boolean mHasRegisteredReceiver;\n",
    "    private boolean mHasRegisteredReceiver;\n"
    "    private int mFglListingRetries;\n",
    1,
)
src = replace_once(
    src,
    '''    @Override
    public void onResume() {
        super.onResume();
        File mountPoint = null;
        if (!ExtStorageReceiver.VALUE_PATH_NONE.equals(currentMountPoint))
            mountPoint = new File(currentMountPoint);
        if ((mountPoint != null && !mountPoint.exists())&&!UriUtils.isContentUri(mCurrentDirectory)) {

            Uri uri = DocumentUriBuilder.getUriFromRootPath(getActivity(),currentMountPoint);
            if(uri!=null){
                mCurrentDirectory = uri;
                currentMountPoint = uri.toString();
                listFiles(false);
                LoaderManager.getInstance(this).restartLoader(0, null, this);

            }

        } else {
            IntentFilter iFilter = new IntentFilter();
            iFilter.addAction(Intent.ACTION_MEDIA_EJECT);
            iFilter.addAction(Intent.ACTION_MEDIA_UNMOUNTED);
            iFilter.addDataScheme("file");
            iFilter.addDataScheme(ExtStorageReceiver.ARCHOS_FILE_SCHEME);//new android nougat send UriExposureException when scheme = file
            getActivity().registerReceiver(mSdCardReceiver, iFilter);
            mHasRegisteredReceiver = true;
        }
    }
''',
    '''    @Override
    public void onResume() {
        super.onResume();
        mFglListingRetries = 0;
        // FGL uses Android's already-approved all-files access.  Do not turn a
        // missing mount into a content:// SAF tree or open a document picker.
        IntentFilter iFilter = new IntentFilter();
        iFilter.addAction(Intent.ACTION_MEDIA_EJECT);
        iFilter.addAction(Intent.ACTION_MEDIA_UNMOUNTED);
        iFilter.addDataScheme("file");
        iFilter.addDataScheme(ExtStorageReceiver.ARCHOS_FILE_SCHEME);//new android nougat send UriExposureException when scheme = file
        getActivity().registerReceiver(mSdCardReceiver, iFilter);
        mHasRegisteredReceiver = true;
    }
''',
    "SAF mount fallback",
)
start = src.find("    @Override\n    public void onListingFatalError(")
if start != -1:
    end = src.find("    @Override\n    public void onSaveInstanceState", start)
    if end == -1:
        raise SystemExit("Unexpected upstream source while removing SAF permission dialog")
    src = src[:start] + '''    private boolean retryFglLocalListing() {
        if (mFglListingRetries < 3) {
            int retry = ++mFglListingRetries;
            // Android may still be publishing the local removable volume just
            // after the special-access Settings screen closes. Retry every
            // initial local-listing failure; a raw mount path must never fall
            // through to Nova's remote-folder error page.
            mHandler.removeCallbacksAndMessages(null);
            mHandler.postDelayed(() -> {
                if (isAdded()) listFiles(false);
            }, 300L * retry);
            return true;
        }
        return false;
    }

    @Override
    public void onListingFatalError(Exception e, ListingEngine.ErrorEnum errorCode) {
        if (retryFglLocalListing()) return;
        super.onListingFatalError(e, errorCode);
    }

    @Override
    public void onListingTimeOut() {
        if (retryFglLocalListing()) return;
        super.onListingTimeOut();
    }

''' + src[end:]
start = src.find("    protected String getIndexableRootFolder()")
if start != -1:
    end = src.find("    @Override\n    protected Uri getDefaultDirectory", start)
    if end == -1:
        raise SystemExit("Unexpected upstream source while removing SAF index root")
    src = src[:start] + src[end:]
src = replace_once(
    src,
    '''    @Override
    protected Uri getDefaultDirectory() {
        return Uri.parse(currentMountPoint);
    }
''',
    '''    @Override
    protected Uri getDefaultDirectory() {
        Uri mountUri = Uri.parse(currentMountPoint);
        // Uri.parse("/storage/...") has no scheme. Filecore then treats it as
        // a remote path and emits the misleading network-folder error. Make
        // local removable storage unambiguously file:// before listing.
        return mountUri.getScheme() == null
                ? Uri.fromFile(new File(currentMountPoint)) : mountUri;
    }
''',
    "local mount URI",
)
start = src.find("    @TargetApi(Build.VERSION_CODES.KITKAT)\n    public void onActivityResult(")
if start != -1:
    end = src.rfind("\n}")
    src = src[:start] + src[end:]
ext_storage.write_text(src)

src = folder_browser.read_text()
src = replace_once(
    src,
    '''        //Do not updateAdapterIfReady if onLoadFinished not completed: make sure that the mCursor is null in this case
        if (mCursor != null) updateAdapterIfReady();''',
    '''        // A content-backed folder can have no MediaStore cursor.  Still
        // create NonIndexedVideo entries so it opens Nova's info/scraper flow,
        // rather than Android's generic ACTION_VIEW single-file flow.
        updateAdapterIfReady();''',
    "listing update guard",
)
src = replace_once(
    src,
    '''    protected void updateAdapterIfReady() {
        if (mCursor!=null && mFileList.size()>0) {
            mItemList.clear();
            VideoCursorMapper cursorMapper = new VideoCursorMapper();
            cursorMapper.bindColumns(mCursor);
            // Create a map of the indexed videos
            HashMap<String, Video> indexedVideosMap = new HashMap<>();

            mCursor.moveToFirst();
            while (!mCursor.isAfterLast()) {
                Video video = (Video)cursorMapper.bind(mCursor);
                String key = video.getFilePath();
                indexedVideosMap.put(key, video);
                mCursor.moveToNext();
            }
            // Must not close the cursor here, else it fails when recreating the fragment from backstack (i.e. when back from a sub-directory)
''',
    '''    protected void updateAdapterIfReady() {
        if (mFileList.size()>0) {
            mItemList.clear();
            // Create a map of indexed videos when MediaStore has a cursor.  A
            // SAF/content folder may legitimately have none.
            HashMap<String, Video> indexedVideosMap = new HashMap<>();
            if (mCursor != null) {
                VideoCursorMapper cursorMapper = new VideoCursorMapper();
                cursorMapper.bindColumns(mCursor);
                mCursor.moveToFirst();
                while (!mCursor.isAfterLast()) {
                    Video video = (Video)cursorMapper.bind(mCursor);
                    String key = video.getFilePath();
                    indexedVideosMap.put(key, video);
                    mCursor.moveToNext();
                }
            }
            // Must not close the cursor here, else it fails when recreating the fragment from backstack (i.e. when back from a sub-directory)
''',
    "cursor-only video conversion",
)
folder_browser.write_text(src)
print(f"Patched FGL storage mode: {ext_storage} and {folder_browser}")
