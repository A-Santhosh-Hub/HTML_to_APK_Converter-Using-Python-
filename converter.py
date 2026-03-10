"""
HTML to APK Builder  v3.0
Developed by SANTHOSH A

FIXED IN v3.0:
  - "unclosed string literal" Java compile errors ELIMINATED
  - JS shim written as a clean asset file (bridge.js), not embedded Java strings
  - MainActivity.java uses clean, readable Java — no Python escape conflicts
  - Windows rmtree locking fix retained
  - All download / live-preview / permission fixes from v2 retained
"""

import os, sys, re, shutil, subprocess, platform, logging, stat, time
from pathlib import Path
from datetime import datetime
from html.parser import HTMLParser

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.resolve()
INPUT_DIR    = BASE_DIR / "input_project"
BUILD_DIR    = BASE_DIR / "build" / "android_project"
OUTPUT_DIR   = BASE_DIR / "output"
LOG_DIR      = BASE_DIR / "logs"

APP_NAME     = "MyWebApp"
PACKAGE_NAME = "com.santhosh.generatedapp"
VERSION_CODE = 1
VERSION_NAME = "1.0"
MIN_SDK      = 24
TARGET_SDK   = 34
COMPILE_SDK  = 34

BANNER = """
+--------------------------------------------------------------+
|         HTML  ->  APK  Builder   v3.0                       |
|         Developed by SANTHOSH A                             |
+--------------------------------------------------------------+
"""

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file = LOG_DIR / f"build_{_ts}.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("apk_builder")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1  —  HTML ANALYZER
# ─────────────────────────────────────────────────────────────────────────────
class HTMLFeatureDetector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.features = {
            "internet":       False,
            "images":         False,
            "iframe":         False,
            "external_links": False,
            "local_storage":  False,
            "drag_drop":      False,
            "file_chooser":   False,
            "file_download":  False,
            "live_preview":   False,
            "media":          False,
            "dark_mode":      False,
            "clipboard":      False,
            "scripts":        [],
            "external_urls":  [],
        }
        self._raw = ""

    def feed_html(self, html: str):
        self._raw = html
        self.feed(html)
        self._post_scan()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        src  = a.get("src",  "")
        href = a.get("href", "")

        if tag == "img":
            self.features["images"] = True
            if src.startswith("http"):
                self.features["internet"] = True
        elif tag == "script":
            if src.startswith("http"):
                self.features["internet"] = True
                self.features["scripts"].append(src)
        elif tag == "link":
            if href.startswith("http"):
                self.features["internet"] = True
        elif tag == "iframe":
            self.features["iframe"] = True
            if src.startswith("http"):
                self.features["internet"] = True
        elif tag == "a":
            if "download" in a:
                self.features["file_download"] = True
            if href.startswith("http"):
                self.features["external_links"] = True
                self.features["internet"] = True
                self.features["external_urls"].append(href)
        elif tag in ("video", "audio"):
            self.features["media"] = True
        elif tag == "input":
            if a.get("type", "").lower() == "file":
                self.features["file_chooser"] = True

    def _post_scan(self):
        h = self._raw
        if re.search(r'localStorage|sessionStorage', h):
            self.features["local_storage"] = True
        if re.search(r'draggable|ondrop|ondragover|["\']drop["\']', h):
            self.features["drag_drop"] = True
            self.features["file_chooser"] = True
        if re.search(r'prefers-color-scheme|dark-mode|darkMode|data-theme', h):
            self.features["dark_mode"] = True
        if re.search(r'fetch\s*\(|XMLHttpRequest|axios\.', h):
            self.features["internet"] = True
        if re.search(r'WebSocket', h):
            self.features["internet"] = True
        if re.search(
            r'URL\.createObjectURL|createObjectURL|\.download\s*=|saveAs\s*\('
            r'|FileSaver|Blob\s*\(|data:text|data:application'
            r'|downloadFile|triggerDownload|saveFile|exportFile', h
        ):
            self.features["file_download"] = True
        if re.search(r'window\.open\s*\(|\.open\s*\(\s*["\']', h):
            self.features["live_preview"] = True
        if re.search(r'navigator\.clipboard|execCommand\s*\(\s*["\']copy', h):
            self.features["clipboard"] = True


def analyze_html(html_path: Path) -> dict:
    log.info("=== STEP 1: Analyzing HTML file ===")
    content = html_path.read_text(encoding="utf-8", errors="replace")
    det = HTMLFeatureDetector()
    det.feed_html(content)
    f = det.features
    log.info("Detected features:")
    for k, v in f.items():
        if isinstance(v, bool):
            log.info("   %-22s %s" % (k, "YES" if v else "no"))
    return f


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2  —  ASSET FILE: bridge.js
#
# This file is placed in assets/ and loaded by MainActivity at runtime.
# Writing it as a plain Python string means ZERO Java escape issues.
# ─────────────────────────────────────────────────────────────────────────────
BRIDGE_JS = r"""
/*
 * bridge.js  —  Android WebView helper
 * Developed by SANTHOSH A
 *
 * Injected into every page after load.
 * Provides:
 *   1. window.open() -> Android.openPreview(html)  (Live Preview)
 *   2. <a download href="blob:..."> -> Android.downloadBase64()
 *   3. <a download href="data:..."> -> Android.downloadBase64()
 */
(function () {
  'use strict';

  /* ── 1. Patch window.open ──────────────────────────────────── */
  var _origOpen = window.open;

  window.open = function (url, target, features) {
    /* No URL or blank — caller will document.write() into returned window */
    if (!url || url === '' || url === 'about:blank') {
      var captured = '';
      var fakeWin = {
        document: {
          write:   function (h) { captured += h; },
          writeln: function (h) { captured += h + '\n'; },
          close:   function ()  {
            if (typeof Android !== 'undefined') {
              Android.openPreview(captured);
            }
          }
        },
        close: function () {}
      };
      return fakeWin;
    }

    /* blob: URL — fetch HTML text and preview it */
    if (url.indexOf('blob:') === 0) {
      fetch(url)
        .then(function (r) { return r.text(); })
        .then(function (html) {
          if (typeof Android !== 'undefined') {
            Android.openPreview(html);
          }
        })
        .catch(function () {
          _origOpen.call(window, url, target, features);
        });
      return null;
    }

    /* Anything else (http / https) — open inside WebView as normal */
    return _origOpen.call(window, url, target, features);
  };

  /* ── 2. Intercept <a download> clicks ─────────────────────── */
  document.addEventListener('click', function (e) {
    /* Walk up the DOM to find an <a download> ancestor */
    var node = e.target;
    while (node && node.tagName !== 'A') {
      node = node.parentElement;
    }
    if (!node || !node.hasAttribute('download')) return;

    var href  = node.href  || '';
    var fname = node.getAttribute('download') || 'download';
    if (!fname || fname.trim() === '') fname = 'download';

    /* blob: href */
    if (href.indexOf('blob:') === 0) {
      e.preventDefault();
      e.stopPropagation();
      fetch(href)
        .then(function (r) { return r.blob(); })
        .then(function (b) {
          var reader = new FileReader();
          reader.onload = function () {
            if (typeof Android !== 'undefined') {
              Android.downloadBase64(
                reader.result,
                fname,
                b.type || 'application/octet-stream'
              );
            }
          };
          reader.readAsDataURL(b);
        })
        .catch(function (err) {
          if (typeof Android !== 'undefined') {
            Android.showToast('Download error: ' + err);
          }
        });
      return;
    }

    /* data: href */
    if (href.indexOf('data:') === 0) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof Android !== 'undefined') {
        Android.downloadBase64(href, fname, '');
      }
    }
  }, true);

})();
"""


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3  —  ANDROID FILE GENERATORS
# ─────────────────────────────────────────────────────────────────────────────

def gen_manifest(pkg: str, app_name: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    package="' + pkg + '">\n'
        '\n'
        '    <!-- Networking -->\n'
        '    <uses-permission android:name="android.permission.INTERNET" />\n'
        '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />\n'
        '\n'
        '    <!-- Storage (maxSdkVersion avoids popup on Android 10+) -->\n'
        '    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE"\n'
        '        android:maxSdkVersion="32" />\n'
        '    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE"\n'
        '        android:maxSdkVersion="29" />\n'
        '\n'
        '    <application\n'
        '        android:allowBackup="true"\n'
        '        android:icon="@mipmap/ic_launcher"\n'
        '        android:label="' + app_name + '"\n'
        '        android:roundIcon="@mipmap/ic_launcher_round"\n'
        '        android:supportsRtl="true"\n'
        '        android:theme="@style/AppTheme"\n'
        '        android:usesCleartextTraffic="true"\n'
        '        android:requestLegacyExternalStorage="true"\n'
        '        android:networkSecurityConfig="@xml/network_security_config">\n'
        '\n'
        '        <activity\n'
        '            android:name=".MainActivity"\n'
        '            android:exported="true"\n'
        '            android:windowSoftInputMode="adjustResize"\n'
        '            android:configChanges="orientation|screenSize|keyboardHidden|keyboard">\n'
        '            <intent-filter>\n'
        '                <action android:name="android.intent.action.MAIN" />\n'
        '                <category android:name="android.intent.category.LAUNCHER" />\n'
        '            </intent-filter>\n'
        '        </activity>\n'
        '\n'
        '        <provider\n'
        '            android:name="androidx.core.content.FileProvider"\n'
        '            android:authorities="' + pkg + '.fileprovider"\n'
        '            android:exported="false"\n'
        '            android:grantUriPermissions="true">\n'
        '            <meta-data\n'
        '                android:name="android.support.FILE_PROVIDER_PATHS"\n'
        '                android:resource="@xml/file_provider_paths" />\n'
        '        </provider>\n'
        '\n'
        '    </application>\n'
        '\n'
        '</manifest>\n'
    )


# MainActivity.java  — written as a plain Python string with NO f-string
# so there is zero risk of { } confusion or \n injection.
def gen_main_activity(pkg: str) -> str:
    lines = [
        "package " + pkg + ";",
        "",
        "import android.annotation.SuppressLint;",
        "import android.app.AlertDialog;",
        "import android.content.ContentValues;",
        "import android.content.Intent;",
        "import android.net.Uri;",
        "import android.os.Build;",
        "import android.os.Bundle;",
        "import android.os.Environment;",
        "import android.provider.MediaStore;",
        "import android.util.Base64;",
        "import android.util.Log;",
        "import android.webkit.JsPromptResult;",
        "import android.webkit.JsResult;",
        "import android.webkit.ValueCallback;",
        "import android.webkit.WebChromeClient;",
        "import android.webkit.WebResourceRequest;",
        "import android.webkit.WebSettings;",
        "import android.webkit.WebView;",
        "import android.webkit.WebViewClient;",
        "import android.widget.FrameLayout;",
        "import android.widget.Toast;",
        "import androidx.appcompat.app.AppCompatActivity;",
        "import androidx.webkit.WebSettingsCompat;",
        "import androidx.webkit.WebViewFeature;",
        "import java.io.*;",
        "import java.net.HttpURLConnection;",
        "import java.net.URL;",
        "",
        "/**",
        " * MainActivity — HTML to APK Builder v3.0",
        " * Developed by SANTHOSH A",
        " *",
        " * Key features:",
        " *   - Loads index.html from assets via WebView",
        " *   - Injects bridge.js (assets/bridge.js) after every page load",
        " *   - AndroidBridge exposes downloadBase64 / downloadText / openPreview / showToast to JS",
        " *   - DownloadListener handles blob:, data:, and https: downloads",
        " *   - window.open() is patched in bridge.js -> openPreview() shows a full-screen dialog",
        " *   - No runtime permission dialogs (all pre-declared in AndroidManifest)",
        " */",
        "public class MainActivity extends AppCompatActivity {",
        "",
        "    private static final String TAG = \"APKBuilder\";",
        "    private WebView webView;",
        "    private ValueCallback<Uri[]> mFilePathCallback;",
        "    private String bridgeJs = null;   // loaded once from assets/bridge.js",
        "",
        "    // =========================================================",
        "    // JavaScript -> Java bridge",
        "    // =========================================================",
        "    public class AndroidBridge {",
        "",
        "        /** Download a base64 data string (or data URI) as a file. */",
        "        @android.webkit.JavascriptInterface",
        "        public void downloadBase64(String base64Data, String fileName, String mimeType) {",
        "            Log.d(TAG, \"downloadBase64: \" + fileName);",
        "            try {",
        "                byte[] bytes;",
        "                if (base64Data.contains(\",\")) {",
        "                    String pure = base64Data.substring(base64Data.indexOf(\",\") + 1);",
        "                    bytes = Base64.decode(pure, Base64.DEFAULT);",
        "                } else {",
        "                    bytes = Base64.decode(base64Data, Base64.DEFAULT);",
        "                }",
        "                saveBytes(bytes, fileName, mimeType);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"downloadBase64 failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }",
        "",
        "        /** Download a plain UTF-8 text string as a file. */",
        "        @android.webkit.JavascriptInterface",
        "        public void downloadText(String text, String fileName, String mimeType) {",
        "            Log.d(TAG, \"downloadText: \" + fileName);",
        "            try {",
        "                byte[] bytes = text.getBytes(\"UTF-8\");",
        "                String safeName = (fileName != null && !fileName.isEmpty()) ? fileName : \"download.txt\";",
        "                String safeMime = (mimeType != null && !mimeType.isEmpty()) ? mimeType : \"text/plain\";",
        "                saveBytes(bytes, safeName, safeMime);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"downloadText failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }",
        "",
        "        /** Show a native Android Toast from JavaScript. */",
        "        @android.webkit.JavascriptInterface",
        "        public void showToast(String message) {",
        "            showToastOnUi(message);",
        "        }",
        "",
        "        /** Open an HTML string as a full-screen live preview dialog. */",
        "        @android.webkit.JavascriptInterface",
        "        public void openPreview(String htmlContent) {",
        "            Log.d(TAG, \"openPreview: \" + (htmlContent != null ? htmlContent.length() : 0) + \" chars\");",
        "            final String html = htmlContent;",
        "            runOnUiThread(new Runnable() {",
        "                @Override public void run() { showPreviewDialog(html); }",
        "            });",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // onCreate",
        "    // =========================================================",
        "    @SuppressLint({\"SetJavaScriptEnabled\", \"AddJavascriptInterface\"})",
        "    @Override",
        "    protected void onCreate(Bundle savedInstanceState) {",
        "        super.onCreate(savedInstanceState);",
        "        setContentView(R.layout.activity_main);",
        "",
        "        // Load bridge.js once from assets",
        "        bridgeJs = loadAssetText(\"bridge.js\");",
        "",
        "        webView = findViewById(R.id.webview);",
        "        WebSettings s = webView.getSettings();",
        "",
        "        s.setJavaScriptEnabled(true);",
        "        s.setDomStorageEnabled(true);",
        "        s.setDatabaseEnabled(true);",
        "        s.setAllowFileAccess(true);",
        "        s.setAllowContentAccess(true);",
        "        s.setAllowFileAccessFromFileURLs(true);",
        "        s.setAllowUniversalAccessFromFileURLs(true);",
        "        s.setMediaPlaybackRequiresUserGesture(false);",
        "        s.setLoadWithOverviewMode(true);",
        "        s.setUseWideViewPort(true);",
        "        s.setSupportZoom(true);",
        "        s.setBuiltInZoomControls(true);",
        "        s.setDisplayZoomControls(false);",
        "        s.setTextZoom(100);",
        "        s.setCacheMode(WebSettings.LOAD_DEFAULT);",
        "        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);",
        "",
        "        // Dark mode: let the HTML handle its own theming",
        "        if (WebViewFeature.isFeatureSupported(WebViewFeature.FORCE_DARK)) {",
        "            WebSettingsCompat.setForceDark(s, WebSettingsCompat.FORCE_DARK_OFF);",
        "        }",
        "",
        "        // Attach the JS <-> Java bridge",
        "        webView.addJavascriptInterface(new AndroidBridge(), \"Android\");",
        "",
        "        // WebViewClient",
        "        webView.setWebViewClient(new WebViewClient() {",
        "            @Override",
        "            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {",
        "                String url = request.getUrl().toString();",
        "                // blob: URLs cannot be navigated to — intercept via JS evaluation",
        "                if (url.startsWith(\"blob:\")) {",
        "                    interceptBlobDownload(url);",
        "                    return true;",
        "                }",
        "                return false; // load everything else inside the WebView",
        "            }",
        "",
        "            @Override",
        "            public void onPageFinished(WebView view, String url) {",
        "                // Inject bridge.js after every page load",
        "                if (bridgeJs != null && !bridgeJs.isEmpty()) {",
        "                    view.evaluateJavascript(bridgeJs, null);",
        "                }",
        "            }",
        "        });",
        "",
        "        // WebChromeClient — file chooser + JS dialogs",
        "        webView.setWebChromeClient(new WebChromeClient() {",
        "",
        "            @Override",
        "            public boolean onShowFileChooser(WebView wv,",
        "                    ValueCallback<Uri[]> filePathCallback,",
        "                    FileChooserParams fileChooserParams) {",
        "                if (mFilePathCallback != null) {",
        "                    mFilePathCallback.onReceiveValue(null);",
        "                }",
        "                mFilePathCallback = filePathCallback;",
        "                Intent intent = fileChooserParams.createIntent();",
        "                try {",
        "                    startActivityForResult(intent, 1001);",
        "                } catch (Exception e) {",
        "                    mFilePathCallback = null;",
        "                    return false;",
        "                }",
        "                return true;",
        "            }",
        "",
        "            @Override",
        "            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {",
        "                new AlertDialog.Builder(MainActivity.this)",
        "                    .setMessage(message)",
        "                    .setPositiveButton(\"OK\", (d, w) -> result.confirm())",
        "                    .setOnCancelListener(d -> result.cancel())",
        "                    .show();",
        "                return true;",
        "            }",
        "",
        "            @Override",
        "            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {",
        "                new AlertDialog.Builder(MainActivity.this)",
        "                    .setMessage(message)",
        "                    .setPositiveButton(\"OK\",     (d, w) -> result.confirm())",
        "                    .setNegativeButton(\"Cancel\", (d, w) -> result.cancel())",
        "                    .setOnCancelListener(d -> result.cancel())",
        "                    .show();",
        "                return true;",
        "            }",
        "",
        "            @Override",
        "            public boolean onJsPrompt(WebView view, String url, String message,",
        "                                      String defaultValue, JsPromptResult result) {",
        "                android.widget.EditText input = new android.widget.EditText(MainActivity.this);",
        "                input.setText(defaultValue);",
        "                new AlertDialog.Builder(MainActivity.this)",
        "                    .setMessage(message)",
        "                    .setView(input)",
        "                    .setPositiveButton(\"OK\",     (d, w) -> result.confirm(input.getText().toString()))",
        "                    .setNegativeButton(\"Cancel\", (d, w) -> result.cancel())",
        "                    .setOnCancelListener(d -> result.cancel())",
        "                    .show();",
        "                return true;",
        "            }",
        "        });",
        "",
        "        // DownloadListener — catches blob:, data:, https: downloads",
        "        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {",
        "            Log.d(TAG, \"DownloadListener: \" + url.substring(0, Math.min(60, url.length())));",
        "            if (url.startsWith(\"data:\")) {",
        "                handleDataUriDownload(url, mimeType, contentDisposition);",
        "            } else if (url.startsWith(\"blob:\")) {",
        "                interceptBlobDownload(url);",
        "            } else {",
        "                downloadUrlInBackground(url, userAgent, mimeType, contentDisposition);",
        "            }",
        "        });",
        "",
        "        webView.loadUrl(\"file:///android_asset/index.html\");",
        "    }",
        "",
        "    // =========================================================",
        "    // Live Preview dialog",
        "    // =========================================================",
        "    private void showPreviewDialog(String htmlContent) {",
        "        WebView preview = new WebView(this);",
        "        WebSettings ps = preview.getSettings();",
        "        ps.setJavaScriptEnabled(true);",
        "        ps.setDomStorageEnabled(true);",
        "        ps.setAllowFileAccess(true);",
        "        ps.setAllowUniversalAccessFromFileURLs(true);",
        "        ps.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);",
        "        preview.setWebViewClient(new WebViewClient());",
        "",
        "        String html = (htmlContent != null && !htmlContent.trim().isEmpty())",
        "            ? htmlContent : \"<html><body><p>Empty preview</p></body></html>\";",
        "",
        "        preview.loadDataWithBaseURL(",
        "            \"file:///android_asset/\",",
        "            html,",
        "            \"text/html\",",
        "            \"UTF-8\",",
        "            null",
        "        );",
        "",
        "        FrameLayout container = new FrameLayout(this);",
        "        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(",
        "            FrameLayout.LayoutParams.MATCH_PARENT,",
        "            FrameLayout.LayoutParams.MATCH_PARENT",
        "        );",
        "        preview.setLayoutParams(lp);",
        "        container.addView(preview);",
        "",
        "        AlertDialog dlg = new AlertDialog.Builder(this)",
        "            .setTitle(\"Live Preview\")",
        "            .setView(container)",
        "            .setPositiveButton(\"Close\", null)",
        "            .create();",
        "        dlg.show();",
        "        if (dlg.getWindow() != null) {",
        "            dlg.getWindow().setLayout(",
        "                android.view.WindowManager.LayoutParams.MATCH_PARENT,",
        "                (int)(getResources().getDisplayMetrics().heightPixels * 0.92)",
        "            );",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // Download helpers",
        "    // =========================================================",
        "",
        "    /** Use JS evaluation to fetch a blob: URL and hand bytes to AndroidBridge. */",
        "    private void interceptBlobDownload(String blobUrl) {",
        "        String escaped = blobUrl.replace(\"\\\\\", \"\\\\\\\\\").replace(\"'\", \"\\\\'\");",
        "        String js =",
        "            \"(function(){\" +",
        "            \"  fetch('\" + escaped + \"')\" +",
        "            \"    .then(function(r){ return r.blob(); })\" +",
        "            \"    .then(function(b){\" +",
        "            \"      var rd = new FileReader();\" +",
        "            \"      rd.onload = function(){\" +",
        "            \"        if(typeof Android!=='undefined')\" +",
        "            \"          Android.downloadBase64(rd.result,'download',b.type||'application/octet-stream');\" +",
        "            \"      };\" +",
        "            \"      rd.readAsDataURL(b);\" +",
        "            \"    })\" +",
        "            \"    .catch(function(e){ if(typeof Android!=='undefined') Android.showToast('Blob error: '+e); });\" +",
        "            \"})();\";",
        "        webView.evaluateJavascript(js, null);",
        "    }",
        "",
        "    private void handleDataUriDownload(String dataUri, String mimeType, String contentDisposition) {",
        "        new Thread(() -> {",
        "            try {",
        "                String[] parts = dataUri.split(\",\", 2);",
        "                String header  = parts[0];",
        "                String body    = parts.length > 1 ? parts[1] : \"\";",
        "                String mime    = (mimeType != null && !mimeType.isEmpty()) ? mimeType",
        "                    : (header.contains(\":\") ? header.split(\":\")[1].split(\";\")[0] : \"application/octet-stream\");",
        "                String ext     = extensionForMime(mime);",
        "                String fname   = extractFilename(contentDisposition, \"download\" + ext);",
        "                byte[] bytes;",
        "                if (header.contains(\"base64\")) {",
        "                    bytes = Base64.decode(body, Base64.DEFAULT);",
        "                } else {",
        "                    bytes = java.net.URLDecoder.decode(body, \"UTF-8\").getBytes(\"UTF-8\");",
        "                }",
        "                saveBytes(bytes, fname, mime);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"data URI download failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }).start();",
        "    }",
        "",
        "    private void downloadUrlInBackground(String urlStr, String userAgent, String mimeType, String contentDisposition) {",
        "        new Thread(() -> {",
        "            try {",
        "                URL url = new URL(urlStr);",
        "                HttpURLConnection conn = (HttpURLConnection) url.openConnection();",
        "                conn.setRequestProperty(\"User-Agent\", userAgent != null ? userAgent : \"Android\");",
        "                conn.connect();",
        "                String cd   = conn.getHeaderField(\"Content-Disposition\");",
        "                String ct   = conn.getHeaderField(\"Content-Type\");",
        "                String mime = (ct != null && !ct.isEmpty()) ? ct.split(\";\")[0].trim()",
        "                    : (mimeType != null ? mimeType : \"application/octet-stream\");",
        "                String fname = extractFilename(cd != null ? cd : contentDisposition,",
        "                    \"download\" + extensionForMime(mime));",
        "                ByteArrayOutputStream baos = new ByteArrayOutputStream();",
        "                InputStream is = conn.getInputStream();",
        "                byte[] buf = new byte[8192];",
        "                int n;",
        "                while ((n = is.read(buf)) != -1) baos.write(buf, 0, n);",
        "                is.close();",
        "                conn.disconnect();",
        "                saveBytes(baos.toByteArray(), fname, mime);",
        "            } catch (Exception e) {",
        "                Log.e(TAG, \"URL download failed\", e);",
        "                showToastOnUi(\"Download failed: \" + e.getMessage());",
        "            }",
        "        }).start();",
        "    }",
        "",
        "    /**",
        "     * Save bytes to Downloads folder.",
        "     * Android 10+ -> MediaStore (Scoped Storage, no permission dialog).",
        "     * Android 7-9 -> direct file write.",
        "     */",
        "    private void saveBytes(byte[] bytes, String fileName, String mimeType) {",
        "        try {",
        "            String safeMime = (mimeType != null && !mimeType.isEmpty()) ? mimeType : \"application/octet-stream\";",
        "            String safeFile = (fileName != null && !fileName.trim().isEmpty()) ? fileName.trim() : \"download.bin\";",
        "",
        "            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {",
        "                ContentValues cv = new ContentValues();",
        "                cv.put(MediaStore.Downloads.DISPLAY_NAME, safeFile);",
        "                cv.put(MediaStore.Downloads.MIME_TYPE, safeMime);",
        "                cv.put(MediaStore.Downloads.IS_PENDING, 1);",
        "                Uri col = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);",
        "                Uri uri = getContentResolver().insert(col, cv);",
        "                if (uri != null) {",
        "                    try (OutputStream os = getContentResolver().openOutputStream(uri)) {",
        "                        if (os != null) os.write(bytes);",
        "                    }",
        "                    cv.clear();",
        "                    cv.put(MediaStore.Downloads.IS_PENDING, 0);",
        "                    getContentResolver().update(uri, cv, null, null);",
        "                    showToastOnUi(\"Saved to Downloads: \" + safeFile);",
        "                }",
        "            } else {",
        "                File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);",
        "                dir.mkdirs();",
        "                File out = new File(dir, safeFile);",
        "                try (FileOutputStream fos = new FileOutputStream(out)) {",
        "                    fos.write(bytes);",
        "                }",
        "                showToastOnUi(\"Saved: \" + out.getAbsolutePath());",
        "            }",
        "        } catch (Exception e) {",
        "            Log.e(TAG, \"saveBytes failed\", e);",
        "            showToastOnUi(\"Save failed: \" + e.getMessage());",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // Utility helpers",
        "    // =========================================================",
        "",
        "    private void showToastOnUi(String message) {",
        "        runOnUiThread(() -> Toast.makeText(this, message, Toast.LENGTH_LONG).show());",
        "    }",
        "",
        "    /** Read a text file from assets/ into a String. */",
        "    private String loadAssetText(String assetName) {",
        "        try (InputStream is = getAssets().open(assetName);",
        "             BufferedReader br = new BufferedReader(new InputStreamReader(is, \"UTF-8\"))) {",
        "            StringBuilder sb = new StringBuilder();",
        "            String line;",
        "            while ((line = br.readLine()) != null) {",
        "                sb.append(line).append('\\n');",
        "            }",
        "            return sb.toString();",
        "        } catch (Exception e) {",
        "            Log.e(TAG, \"Failed to load asset: \" + assetName, e);",
        "            return null;",
        "        }",
        "    }",
        "",
        "    private String extensionForMime(String mime) {",
        "        if (mime == null) return \".bin\";",
        "        switch (mime.trim().toLowerCase().split(\";\")[0].trim()) {",
        "            case \"text/html\":              return \".html\";",
        "            case \"text/plain\":              return \".txt\";",
        "            case \"text/css\":               return \".css\";",
        "            case \"text/javascript\":",
        "            case \"application/javascript\": return \".js\";",
        "            case \"application/json\":       return \".json\";",
        "            case \"application/pdf\":        return \".pdf\";",
        "            case \"application/zip\":        return \".zip\";",
        "            case \"image/png\":              return \".png\";",
        "            case \"image/jpeg\":             return \".jpg\";",
        "            case \"image/gif\":              return \".gif\";",
        "            case \"image/svg+xml\":          return \".svg\";",
        "            default:                       return \".bin\";",
        "        }",
        "    }",
        "",
        "    private String extractFilename(String contentDisposition, String fallback) {",
        "        if (contentDisposition != null) {",
        "            java.util.regex.Matcher m = java.util.regex.Pattern",
        "                .compile(\"filename\\\\*?=[\\\"']?([^\\\"';\\\\n]+)[\\\"']?\",",
        "                         java.util.regex.Pattern.CASE_INSENSITIVE)",
        "                .matcher(contentDisposition);",
        "            if (m.find()) return m.group(1).trim();",
        "        }",
        "        return (fallback != null) ? fallback : \"download.bin\";",
        "    }",
        "",
        "    // =========================================================",
        "    // File chooser result",
        "    // =========================================================",
        "    @Override",
        "    protected void onActivityResult(int requestCode, int resultCode, Intent data) {",
        "        super.onActivityResult(requestCode, resultCode, data);",
        "        if (requestCode == 1001) {",
        "            if (mFilePathCallback == null) return;",
        "            Uri[] results = null;",
        "            if (resultCode == RESULT_OK && data != null) {",
        "                if (data.getClipData() != null) {",
        "                    int count = data.getClipData().getItemCount();",
        "                    results = new Uri[count];",
        "                    for (int i = 0; i < count; i++) {",
        "                        results[i] = data.getClipData().getItemAt(i).getUri();",
        "                    }",
        "                } else if (data.getDataString() != null) {",
        "                    results = new Uri[]{Uri.parse(data.getDataString())};",
        "                }",
        "            }",
        "            mFilePathCallback.onReceiveValue(results);",
        "            mFilePathCallback = null;",
        "        }",
        "    }",
        "",
        "    // =========================================================",
        "    // Back button",
        "    // =========================================================",
        "    @Override",
        "    public void onBackPressed() {",
        "        if (webView != null && webView.canGoBack()) {",
        "            webView.goBack();",
        "        } else {",
        "            super.onBackPressed();",
        "        }",
        "    }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def gen_build_gradle(pkg: str) -> str:
    lines = [
        "plugins {",
        "    id 'com.android.application'",
        "}",
        "",
        "android {",
        "    compileSdk " + str(COMPILE_SDK),
        "    namespace '" + pkg + "'",
        "",
        "    defaultConfig {",
        "        applicationId \"" + pkg + "\"",
        "        minSdk " + str(MIN_SDK),
        "        targetSdk " + str(TARGET_SDK),
        "        versionCode " + str(VERSION_CODE),
        "        versionName \"" + VERSION_NAME + "\"",
        "        multiDexEnabled true",
        "    }",
        "",
        "    buildTypes {",
        "        release {",
        "            minifyEnabled false",
        "            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'",
        "            signingConfig signingConfigs.debug",
        "        }",
        "        debug {",
        "            debuggable true",
        "        }",
        "    }",
        "",
        "    compileOptions {",
        "        sourceCompatibility JavaVersion.VERSION_1_8",
        "        targetCompatibility JavaVersion.VERSION_1_8",
        "    }",
        "",
        "    packagingOptions {",
        "        resources {",
        "            excludes += ['/META-INF/**']",
        "        }",
        "    }",
        "}",
        "",
        "dependencies {",
        "    implementation 'androidx.appcompat:appcompat:1.6.1'",
        "    implementation 'com.google.android.material:material:1.11.0'",
        "    implementation 'androidx.webkit:webkit:1.9.0'",
        "    implementation 'androidx.core:core:1.12.0'",
        "    implementation 'androidx.multidex:multidex:2.0.1'",
        "}",
    ]
    return "\n".join(lines) + "\n"


def gen_settings_gradle(app_name: str) -> str:
    lines = [
        "pluginManagement {",
        "    repositories {",
        "        google()",
        "        mavenCentral()",
        "        gradlePluginPortal()",
        "    }",
        "}",
        "dependencyResolutionManagement {",
        "    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)",
        "    repositories {",
        "        google()",
        "        mavenCentral()",
        "    }",
        "}",
        "rootProject.name = \"" + app_name + "\"",
        "include ':app'",
    ]
    return "\n".join(lines) + "\n"


def gen_root_build_gradle() -> str:
    return (
        "plugins {\n"
        "    id 'com.android.application' version '8.2.2' apply false\n"
        "}\n"
    )


def gen_layout() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<RelativeLayout xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:layout_width="match_parent"\n'
        '    android:layout_height="match_parent"\n'
        '    android:background="#FFFFFF">\n'
        '\n'
        '    <WebView\n'
        '        android:id="@+id/webview"\n'
        '        android:layout_width="match_parent"\n'
        '        android:layout_height="match_parent" />\n'
        '\n'
        '</RelativeLayout>\n'
    )


def gen_styles() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources>\n'
        '    <style name="AppTheme" parent="Theme.AppCompat.Light.NoActionBar">\n'
        '        <item name="colorPrimary">#2196F3</item>\n'
        '        <item name="colorPrimaryDark">#1976D2</item>\n'
        '        <item name="colorAccent">#03DAC5</item>\n'
        '        <item name="android:windowBackground">@android:color/white</item>\n'
        '    </style>\n'
        '</resources>\n'
    )


def gen_colors() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<resources>\n'
        '    <color name="ic_launcher_background">#2196F3</color>\n'
        '</resources>\n'
    )


def gen_network_security() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<network-security-config>\n'
        '    <base-config cleartextTrafficPermitted="true">\n'
        '        <trust-anchors>\n'
        '            <certificates src="system" />\n'
        '            <certificates src="user" />\n'
        '        </trust-anchors>\n'
        '    </base-config>\n'
        '    <domain-config cleartextTrafficPermitted="true">\n'
        '        <domain includeSubdomains="true">localhost</domain>\n'
        '        <domain includeSubdomains="true">127.0.0.1</domain>\n'
        '    </domain-config>\n'
        '</network-security-config>\n'
    )


def gen_file_provider_paths() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<paths>\n'
        '    <external-path        name="external"       path="." />\n'
        '    <external-files-path  name="external_files" path="." />\n'
        '    <files-path           name="internal"        path="." />\n'
        '    <cache-path           name="cache"           path="." />\n'
        '    <external-cache-path  name="external_cache"  path="." />\n'
        '</paths>\n'
    )


def gen_proguard() -> str:
    return (
        '-keep class * extends android.webkit.WebViewClient { *; }\n'
        '-keep class * extends android.webkit.WebChromeClient { *; }\n'
        '-keepclassmembers class * {\n'
        '    @android.webkit.JavascriptInterface <methods>;\n'
        '}\n'
    )


def gen_gradle_properties() -> str:
    return (
        'org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8\n'
        'android.useAndroidX=true\n'
        'android.enableJetifier=true\n'
    )


def gen_gradle_wrapper_props() -> str:
    return (
        'distributionBase=GRADLE_USER_HOME\n'
        'distributionPath=wrapper/dists\n'
        'distributionUrl=https\\://services.gradle.org/distributions/gradle-8.2-bin.zip\n'
        'zipStoreBase=GRADLE_USER_HOME\n'
        'zipStorePath=wrapper/dists\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# WINDOWS-SAFE DIRECTORY REMOVAL
# ─────────────────────────────────────────────────────────────────────────────
def _force_remove(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception as e:
        log.debug("Force-remove fallback failed for %s: %s" % (path, e))


def robust_rmtree(path: Path, retries: int = 5, delay: float = 0.5):
    if not path.exists():
        return
    if platform.system() == "Windows":
        try:
            raw = str(path.resolve())
            if not raw.startswith("\\\\?\\"):
                raw = "\\\\?\\" + raw
            path = Path(raw)
        except Exception:
            pass
    for attempt in range(1, retries + 1):
        try:
            shutil.rmtree(path, onerror=_force_remove)
            if not path.exists():
                return
        except Exception as e:
            log.debug("rmtree attempt %d/%d failed: %s" % (attempt, retries, e))
            if attempt < retries:
                time.sleep(delay)
    if path.exists():
        tombstone = path.parent / (path.name + "_old_" + str(int(time.time())))
        try:
            path.rename(tombstone)
            log.warning("Could not delete old build dir; renamed to: " + tombstone.name)
            log.warning("You can delete it manually later.")
        except Exception as e:
            log.warning("Could not rename old build dir: %s — will overwrite files." % e)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4  —  BUILD PROJECT TREE
# ─────────────────────────────────────────────────────────────────────────────
def build_android_project(features: dict, html_path: Path,
                           pkg: str = PACKAGE_NAME,
                           app_name: str = APP_NAME) -> Path:
    log.info("=== STEP 3: Building Android project structure ===")
    proj = BUILD_DIR
    if proj.exists():
        log.info("   Cleaning previous build directory...")
        robust_rmtree(proj)

    pkg_path = pkg.replace(".", "/")

    dirs = [
        proj / "app/src/main/java" / pkg_path,
        proj / "app/src/main/res/layout",
        proj / "app/src/main/res/values",
        proj / "app/src/main/res/xml",
        proj / "app/src/main/res/mipmap-hdpi",
        proj / "app/src/main/res/mipmap-mdpi",
        proj / "app/src/main/res/mipmap-xhdpi",
        proj / "app/src/main/res/mipmap-xxhdpi",
        proj / "app/src/main/res/mipmap-xxxhdpi",
        proj / "app/src/main/assets",
        proj / "gradle/wrapper",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    def w(path: Path, content: str):
        path.write_text(content, encoding="utf-8")
        log.debug("   Wrote: " + str(path.relative_to(proj)))

    # Java source
    w(proj / "app/src/main/java" / pkg_path / "MainActivity.java",
      gen_main_activity(pkg))

    # Manifest
    w(proj / "app/src/main/AndroidManifest.xml",
      gen_manifest(pkg, app_name))

    # Resources
    w(proj / "app/src/main/res/layout/activity_main.xml",       gen_layout())
    w(proj / "app/src/main/res/values/styles.xml",               gen_styles())
    w(proj / "app/src/main/res/values/colors.xml",               gen_colors())
    w(proj / "app/src/main/res/xml/network_security_config.xml", gen_network_security())
    w(proj / "app/src/main/res/xml/file_provider_paths.xml",     gen_file_provider_paths())

    # Gradle
    w(proj / "app/build.gradle",          gen_build_gradle(pkg))
    w(proj / "settings.gradle",           gen_settings_gradle(app_name))
    w(proj / "build.gradle",              gen_root_build_gradle())
    w(proj / "gradle.properties",         gen_gradle_properties())
    w(proj / "app/proguard-rules.pro",    gen_proguard())
    w(proj / "gradle/wrapper/gradle-wrapper.properties", gen_gradle_wrapper_props())

    # gradlew scripts
    gw = proj / "gradlew"
    gw.write_text("#!/usr/bin/env sh\nexec \"$JAVA_HOME/bin/java\" -jar \"$0.jar\" \"$@\"\n")
    gw.chmod(0o755)
    w(proj / "gradlew.bat", "@echo off\njava -jar gradlew.jar %*\n")

    # Assets: HTML + JS bridge
    shutil.copy2(html_path, proj / "app/src/main/assets/index.html")
    w(proj / "app/src/main/assets/bridge.js", BRIDGE_JS)

    log.info("   Copied HTML  -> assets/index.html")
    log.info("   Wrote bridge -> assets/bridge.js")
    log.info("   Android project structure created OK")
    return proj


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5  —  COMPILE APK
# ─────────────────────────────────────────────────────────────────────────────
def find_sdk() -> Path | None:
    candidates = [
        os.environ.get("ANDROID_HOME", ""),
        os.environ.get("ANDROID_SDK_ROOT", ""),
        str(Path.home() / "Android/Sdk"),
        str(Path.home() / "AppData/Local/Android/Sdk"),
        "/opt/android-sdk",
        "/usr/local/android-sdk",
    ]
    for c in candidates:
        p = Path(c)
        if p.exists() and (p / "platforms").exists():
            return p
    return None


def compile_apk(project_dir: Path) -> bool:
    log.info("=== STEP 4: Compiling APK ===")
    sdk = find_sdk()
    if sdk:
        log.info("   Android SDK: " + str(sdk))
        (project_dir / "local.properties").write_text(
            "sdk.dir=" + sdk.as_posix() + "\n", encoding="utf-8")
    else:
        log.warning("   Android SDK not found — skipping Gradle build.")
        log.warning("   Set ANDROID_HOME and re-run, or open the project in Android Studio.")
        return False

    is_win = platform.system() == "Windows"
    cmd = ["gradlew.bat" if is_win else "./gradlew", "assembleDebug", "--stacktrace"]
    log.info("   Running: " + " ".join(cmd))
    try:
        result = subprocess.run(cmd, cwd=project_dir, timeout=600)
        if result.returncode != 0:
            log.error("   Gradle build FAILED.")
            return False
    except FileNotFoundError:
        log.error("   gradlew not found. Ensure JDK 17+ is on PATH.")
        return False
    except subprocess.TimeoutExpired:
        log.error("   Build timed out.")
        return False

    apks = list(project_dir.glob("app/build/outputs/apk/**/*.apk"))
    if not apks:
        log.error("   No APK found after build.")
        return False

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUTPUT_DIR / "app.apk"
    shutil.copy2(apks[0], dest)
    log.info("   APK -> " + str(dest) + "  (%.1f KB)" % (dest.stat().st_size / 1024))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6  —  SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
def print_summary(features: dict, project_dir: Path, apk_built: bool):
    log.info("")
    log.info("=" * 62)
    log.info("  BUILD SUMMARY  —  HTML to APK Builder v3.0")
    log.info("=" * 62)
    log.info("  Package  : " + PACKAGE_NAME)
    log.info("  App Name : " + APP_NAME)
    log.info("  Min SDK  : " + str(MIN_SDK) + "  (Android 7.0+)")
    log.info("  Target   : API " + str(TARGET_SDK))
    log.info("")
    log.info("  Detected capabilities:")
    for k, v in features.items():
        if isinstance(v, bool) and v:
            log.info("    YES  " + k)
    log.info("")
    log.info("  v3.0 fixes:")
    log.info("    - NO Java compile errors (JS shim is assets/bridge.js)")
    log.info("    - File downloads: blob, data URI, https all handled")
    log.info("    - Live Preview: window.open() -> full-screen dialog")
    log.info("    - No runtime permission popups")
    log.info("    - Windows rmtree locking handled")
    log.info("")
    if apk_built:
        log.info("  APK: " + str(OUTPUT_DIR / "app.apk"))
        log.info("  Install: adb install output/app.apk")
    else:
        log.info("  Open in Android Studio: " + str(project_dir))
        log.info("  Then: Build > Build APK(s)")
    log.info("")
    log.info("  Log: " + str(log_file))
    log.info("=" * 62)
    log.info("  Developed by SANTHOSH A")
    log.info("=" * 62)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(BANNER)
    html_path = INPUT_DIR / "index.html"
    if not html_path.exists():
        log.error("Missing: " + str(html_path))
        log.error("Place your index.html inside input_project/ and re-run.")
        sys.exit(1)

    log.info("Input : " + str(html_path))
    log.info("Pkg   : " + PACKAGE_NAME + "  |  App: " + APP_NAME)
    log.info("")

    features    = analyze_html(html_path)
    project_dir = build_android_project(features, html_path)
    apk_built   = compile_apk(project_dir)
    print_summary(features, project_dir, apk_built)


if __name__ == "__main__":
    main()
