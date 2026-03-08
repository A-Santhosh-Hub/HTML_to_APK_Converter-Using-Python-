"""
╔══════════════════════════════════════════════════════════════╗
║              HTML → APK Builder                              ║
║              Developed by SANTHOSH A                         ║
╚══════════════════════════════════════════════════════════════╝

Converts a single HTML file (with internal CSS/JS) into a
fully installable Android APK using WebView.
"""

import os
import sys
import re
import shutil
import subprocess
import platform
import json
import logging
from pathlib import Path
from datetime import datetime
from html.parser import HTMLParser

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent.resolve()
INPUT_DIR      = BASE_DIR / "input_project"
BUILD_DIR      = BASE_DIR / "build" / "android_project"
OUTPUT_DIR     = BASE_DIR / "output"
LOG_DIR        = BASE_DIR / "logs"

APP_NAME       = "MyWebApp"
PACKAGE_NAME   = "com.santhosh.generatedapp"
VERSION_CODE   = 1
VERSION_NAME   = "1.0"
MIN_SDK        = 21
TARGET_SDK     = 34
COMPILE_SDK    = 34

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║              HTML → APK Builder                              ║
║              Developed by SANTHOSH A                         ║
╚══════════════════════════════════════════════════════════════╝
"""

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("apk_builder")


# ─────────────────────────────────────────────
# STEP 1: HTML ANALYZER
# ─────────────────────────────────────────────
class HTMLFeatureDetector(HTMLParser):
    """Scans the HTML file and identifies required Android capabilities."""

    def __init__(self):
        super().__init__()
        self.features = {
            "internet":        False,
            "images":          False,
            "iframe":          False,
            "external_links":  False,
            "local_storage":   False,
            "drag_drop":       False,
            "file_chooser":    False,
            "media":           False,
            "dark_mode":       False,
            "scripts":         [],
            "external_urls":   [],
        }
        self._raw_html = ""

    def feed_html(self, html: str):
        self._raw_html = html
        self.feed(html)
        self._post_scan()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.features["images"] = True
            src = attrs.get("src", "")
            if src.startswith("http"):
                self.features["internet"] = True

        elif tag in ("script",):
            src = attrs.get("src", "")
            if src.startswith("http"):
                self.features["internet"] = True
                self.features["scripts"].append(src)

        elif tag == "link":
            href = attrs.get("href", "")
            if href.startswith("http"):
                self.features["internet"] = True

        elif tag == "iframe":
            self.features["iframe"] = True
            src = attrs.get("src", "")
            if src.startswith("http"):
                self.features["internet"] = True

        elif tag == "a":
            href = attrs.get("href", "")
            if href.startswith("http"):
                self.features["external_links"] = True
                self.features["internet"] = True
                self.features["external_urls"].append(href)

        elif tag in ("video", "audio"):
            self.features["media"] = True

        elif tag == "input":
            if attrs.get("type", "").lower() == "file":
                self.features["file_chooser"] = True

    def _post_scan(self):
        """Regex-based deeper scan on raw HTML text."""
        html = self._raw_html

        # localStorage / sessionStorage
        if re.search(r'localStorage|sessionStorage', html):
            self.features["local_storage"] = True

        # drag & drop
        if re.search(r'draggable|ondrop|ondragover|addEventListener\s*\(\s*["\']drop', html):
            self.features["drag_drop"] = True
            self.features["file_chooser"] = True

        # dark mode / prefers-color-scheme
        if re.search(r'prefers-color-scheme|dark-mode|darkMode|data-theme', html):
            self.features["dark_mode"] = True

        # fetch / XMLHttpRequest / axios → internet
        if re.search(r'fetch\(|XMLHttpRequest|axios\.', html):
            self.features["internet"] = True

        # WebSocket
        if re.search(r'WebSocket', html):
            self.features["internet"] = True


def analyze_html(html_path: Path) -> dict:
    log.info("━━━ STEP 1: Analyzing HTML file ━━━")
    if not html_path.exists():
        log.error(f"HTML file not found: {html_path}")
        sys.exit(1)

    html_content = html_path.read_text(encoding="utf-8", errors="replace")
    detector = HTMLFeatureDetector()
    detector.feed_html(html_content)
    features = detector.features

    log.info("Detected features:")
    for k, v in features.items():
        if isinstance(v, bool):
            status = "✅ YES" if v else "   no"
            log.info(f"   {k:<20} {status}")
    log.info(f"   external_urls: {len(features['external_urls'])} found")
    log.info(f"   scripts:       {len(features['scripts'])} external scripts")
    return features


# ─────────────────────────────────────────────
# STEP 2: ANDROID PROJECT GENERATOR
# ─────────────────────────────────────────────
def generate_android_manifest(features: dict, pkg: str, app_name: str) -> str:
    """Generate AndroidManifest.xml based on detected features."""
    permissions = []

    if features["internet"]:
        permissions.append('<uses-permission android:name="android.permission.INTERNET" />')

    if features["file_chooser"] or features["drag_drop"]:
        permissions.append('<uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />')
        permissions.append('<uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" />')

    if features["media"]:
        permissions.append('<uses-permission android:name="android.permission.CAMERA" />')
        permissions.append('<uses-permission android:name="android.permission.RECORD_AUDIO" />')

    permissions_xml = "\n    ".join(permissions)

    network_security = ""
    if features["internet"]:
        network_security = 'android:networkSecurityConfig="@xml/network_security_config"'

    return f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{pkg}">

    {permissions_xml}

    <application
        android:allowBackup="true"
        android:icon="@mipmap/ic_launcher"
        android:label="{app_name}"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:supportsRtl="true"
        android:theme="@style/AppTheme"
        android:usesCleartextTraffic="true"
        {network_security}>

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize|keyboardHidden">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

    </application>

</manifest>
"""


def generate_main_activity(features: dict, pkg: str) -> str:
    """Generate MainActivity.java with full WebView configuration."""

    file_chooser_imports = ""
    file_chooser_field = ""
    file_chooser_override = ""
    file_chooser_result = ""

    if features["file_chooser"] or features["drag_drop"]:
        file_chooser_imports = """
import android.content.Intent;
import android.net.Uri;
import android.webkit.ValueCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;"""

        file_chooser_field = """
    private ValueCallback<Uri[]> mFilePathCallback;
    private ActivityResultLauncher<String> filePickerLauncher;"""

        file_chooser_result = """
        filePickerLauncher = registerForActivityResult(
            new ActivityResultContracts.GetContent(),
            uri -> {
                if (mFilePathCallback != null) {
                    mFilePathCallback.onReceiveValue(uri != null ? new Uri[]{uri} : null);
                    mFilePathCallback = null;
                }
            }
        );"""

        file_chooser_override = """
            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback,
                                             FileChooserParams fileChooserParams) {
                if (mFilePathCallback != null) {
                    mFilePathCallback.onReceiveValue(null);
                }
                mFilePathCallback = filePathCallback;
                filePickerLauncher.launch("*/*");
                return true;
            }"""

    dark_mode_code = ""
    if features["dark_mode"]:
        dark_mode_code = """
        // Force WebView to respect system dark mode
        if (WebViewFeature.isFeatureSupported(WebViewFeature.FORCE_DARK)) {
            int nightModeFlags = getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK;
            if (nightModeFlags == Configuration.UI_MODE_NIGHT_YES) {
                WebSettingsCompat.setForceDark(webView.getSettings(), WebSettingsCompat.FORCE_DARK_ON);
            }
        }"""

    return f"""package {pkg};

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.res.Configuration;
import android.os.Bundle;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.appcompat.app.AppCompatActivity;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;{file_chooser_imports}

/**
 * MainActivity — HTML → APK Builder
 * Developed by SANTHOSH A
 *
 * Hosts the bundled index.html inside an Android WebView with
 * full JavaScript, DOM storage, file access, and media support.
 */
public class MainActivity extends AppCompatActivity {{
{file_chooser_field}

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        WebView webView = findViewById(R.id.webview);
        WebSettings settings = webView.getSettings();

        // ── Core JavaScript & Storage ─────────────────────────────
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);

        // ── File & Content Access ─────────────────────────────────
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setAllowFileAccessFromFileURLs(true);
        settings.setAllowUniversalAccessFromFileURLs(true);

        // ── Media & Rendering ─────────────────────────────────────
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);

        // ── Cache ─────────────────────────────────────────────────
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        // ── Dark Mode (if applicable) ─────────────────────────────
        {dark_mode_code}

        // ── File Chooser Result Launcher ──────────────────────────
        {file_chooser_result}

        // ── WebViewClient: keep all navigation inside the app ─────
        webView.setWebViewClient(new WebViewClient() {{
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {{
                // Open all URLs inside the WebView
                return false;
            }}
        }});

        // ── WebChromeClient: dialogs, file chooser, permissions ───
        webView.setWebChromeClient(new WebChromeClient() {{
            {file_chooser_override}
        }});

        // ── Load the bundled HTML ─────────────────────────────────
        webView.loadUrl("file:///android_asset/index.html");
    }}

    @Override
    public void onBackPressed() {{
        WebView webView = findViewById(R.id.webview);
        if (webView.canGoBack()) {{
            webView.goBack();
        }} else {{
            super.onBackPressed();
        }}
    }}
}}
"""


def generate_build_gradle(pkg: str) -> str:
    return f"""plugins {{
    id 'com.android.application'
}}

android {{
    compileSdk {COMPILE_SDK}
    namespace '{pkg}'

    defaultConfig {{
        applicationId "{pkg}"
        minSdk {MIN_SDK}
        targetSdk {TARGET_SDK}
        versionCode {VERSION_CODE}
        versionName "{VERSION_NAME}"
    }}

    buildTypes {{
        release {{
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
            signingConfig signingConfigs.debug   // Use debug key for now
        }}
        debug {{
            debuggable true
        }}
    }}

    compileOptions {{
        sourceCompatibility JavaVersion.VERSION_1_8
        targetCompatibility JavaVersion.VERSION_1_8
    }}

    packagingOptions {{
        resources {{
            excludes += ['/META-INF/**']
        }}
    }}
}}

dependencies {{
    implementation 'androidx.appcompat:appcompat:1.6.1'
    implementation 'com.google.android.material:material:1.11.0'
    implementation 'androidx.webkit:webkit:1.8.0'
}}
"""


def generate_settings_gradle(app_name: str) -> str:
    return f"""pluginManagement {{
    repositories {{
        google()
        mavenCentral()
        gradlePluginPortal()
    }}
}}
dependencyResolutionManagement {{
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {{
        google()
        mavenCentral()
    }}
}}
rootProject.name = "{app_name}"
include ':app'
"""


def generate_root_build_gradle() -> str:
    return """plugins {
    id 'com.android.application' version '8.2.2' apply false
}
"""


def generate_activity_main_layout() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<RelativeLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent">

    <WebView
        android:id="@+id/webview"
        android:layout_width="match_parent"
        android:layout_height="match_parent" />

</RelativeLayout>
"""


def generate_app_theme_xml() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <style name="AppTheme" parent="Theme.AppCompat.Light.NoActionBar">
        <item name="colorPrimary">#2196F3</item>
        <item name="colorPrimaryDark">#1976D2</item>
        <item name="colorAccent">#03DAC5</item>
        <item name="android:windowBackground">@android:color/white</item>
    </style>
</resources>
"""


def generate_network_security_config() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </base-config>
</network-security-config>
"""


def generate_gradle_properties() -> str:
    return """org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
android.enableJetifier=true
"""


def generate_proguard_rules() -> str:
    return """# Add project specific ProGuard rules here.
-keep class * extends android.webkit.WebViewClient
-keep class * extends android.webkit.WebChromeClient
"""


def generate_ic_launcher_xml() -> str:
    """Minimal adaptive icon XML."""
    return """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
"""


def generate_colors_xml() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">#2196F3</color>
</resources>
"""


# ─────────────────────────────────────────────
# STEP 3: BUILD ANDROID PROJECT STRUCTURE
# ─────────────────────────────────────────────
def build_android_project(features: dict, html_path: Path,
                           pkg: str = PACKAGE_NAME,
                           app_name: str = APP_NAME) -> Path:
    log.info("━━━ STEP 3: Building Android project structure ━━━")

    proj = BUILD_DIR
    if proj.exists():
        shutil.rmtree(proj)

    pkg_path = pkg.replace(".", "/")

    # Directory tree
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
        log.debug(f"   Created: {d.relative_to(BASE_DIR)}")

    # ── Source files ───────────────────────────────────────────────
    _write(proj / "app/src/main/java" / pkg_path / "MainActivity.java",
           generate_main_activity(features, pkg))

    _write(proj / "app/src/main/AndroidManifest.xml",
           generate_android_manifest(features, pkg, app_name))

    _write(proj / "app/src/main/res/layout/activity_main.xml",
           generate_activity_main_layout())

    _write(proj / "app/src/main/res/values/styles.xml",
           generate_app_theme_xml())

    _write(proj / "app/src/main/res/values/colors.xml",
           generate_colors_xml())

    if features["internet"]:
        _write(proj / "app/src/main/res/xml/network_security_config.xml",
               generate_network_security_config())

    # ── Gradle files ───────────────────────────────────────────────
    _write(proj / "app/build.gradle",
           generate_build_gradle(pkg))

    _write(proj / "settings.gradle",
           generate_settings_gradle(app_name))

    _write(proj / "build.gradle",
           generate_root_build_gradle())

    _write(proj / "gradle.properties",
           generate_gradle_properties())

    _write(proj / "app/proguard-rules.pro",
           generate_proguard_rules())

    # ── Gradle wrapper ─────────────────────────────────────────────
    _write_gradle_wrapper(proj)

    # ── Copy HTML asset ────────────────────────────────────────────
    dest_html = proj / "app/src/main/assets/index.html"
    shutil.copy2(html_path, dest_html)
    log.info(f"   Copied HTML → assets/index.html")

    log.info("   Android project structure created ✅")
    return proj


def _write(path: Path, content: str):
    path.write_text(content, encoding="utf-8")
    log.debug(f"   Wrote: {path.name}")


def _write_gradle_wrapper(proj: Path):
    props = """distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https\\://services.gradle.org/distributions/gradle-8.2-bin.zip
zipStoreBase=GRADLE_USER_HOME
zipStorePath=wrapper/dists
"""
    _write(proj / "gradle/wrapper/gradle-wrapper.properties", props)

    # gradlew shell script (Linux/macOS)
    gradlew = """#!/usr/bin/env sh
exec "$JAVA_HOME/bin/java" -jar "$0.jar" "$@"
"""
    gradlew_path = proj / "gradlew"
    gradlew_path.write_text(gradlew, encoding="utf-8")
    gradlew_path.chmod(0o755)

    # gradlew.bat (Windows)
    gradlew_bat = """@echo off
java -jar gradlew.jar %*
"""
    _write(proj / "gradlew.bat", gradlew_bat)


# ─────────────────────────────────────────────
# STEP 4: APK COMPILER
# ─────────────────────────────────────────────
def find_sdk() -> Path | None:
    """Try to locate Android SDK automatically."""
    candidates = [
        os.environ.get("ANDROID_HOME", ""),
        os.environ.get("ANDROID_SDK_ROOT", ""),
        str(Path.home() / "Android/Sdk"),
        str(Path.home() / "AppData/Local/Android/Sdk"),   # Windows
        "/opt/android-sdk",
        "/usr/local/android-sdk",
    ]
    for c in candidates:
        p = Path(c)
        if p.exists() and (p / "platforms").exists():
            return p
    return None


def compile_apk(project_dir: Path) -> bool:
    """Run Gradle assembleDebug and collect the APK."""
    log.info("━━━ STEP 4: Compiling APK ━━━")

    sdk = find_sdk()
    if sdk:
        log.info(f"   Android SDK found at: {sdk}")
        # Write local.properties so Gradle can find the SDK
        _write(project_dir / "local.properties",
               f"sdk.dir={sdk.as_posix()}\n")
    else:
        log.warning("   ⚠️  Android SDK not found automatically.")
        log.warning("   Please set ANDROID_HOME environment variable or install Android Studio.")
        log.warning("   Skipping Gradle build — project files are ready in /build/android_project")
        log.info("   You can open the project in Android Studio and build from there.")
        return False

    # Choose correct Gradle wrapper binary
    is_windows = platform.system() == "Windows"
    gradlew_bin = "gradlew.bat" if is_windows else "./gradlew"

    cmd = [gradlew_bin, "assembleDebug", "--stacktrace"]
    log.info(f"   Running: {' '.join(cmd)}")
    log.info("   This may take several minutes on first run (downloads dependencies)…")

    try:
        result = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=False,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            log.error("   Gradle build FAILED. See output above for details.")
            return False
    except FileNotFoundError:
        log.error("   'gradlew' not found. Ensure Java JDK 17+ is installed.")
        return False
    except subprocess.TimeoutExpired:
        log.error("   Build timed out after 10 minutes.")
        return False

    # Find the generated APK
    apk_glob = list(project_dir.glob("app/build/outputs/apk/**/*.apk"))
    if not apk_glob:
        log.error("   APK not found after build. Check Gradle output.")
        return False

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUTPUT_DIR / "app.apk"
    shutil.copy2(apk_glob[0], dest)
    log.info(f"   ✅ APK generated → {dest}")
    log.info(f"   Size: {dest.stat().st_size / 1024:.1f} KB")
    return True


# ─────────────────────────────────────────────
# STEP 5: REPORT
# ─────────────────────────────────────────────
def print_summary(features: dict, project_dir: Path, apk_built: bool):
    log.info("")
    log.info("═" * 62)
    log.info("  BUILD SUMMARY")
    log.info("═" * 62)
    log.info(f"  HTML analyzed   : {INPUT_DIR / 'index.html'}")
    log.info(f"  Android project : {project_dir}")
    log.info(f"  Package name    : {PACKAGE_NAME}")
    log.info(f"  App name        : {APP_NAME}")
    log.info(f"  SDK target      : API {TARGET_SDK}")
    log.info("")
    log.info("  Detected capabilities:")
    for k, v in features.items():
        if isinstance(v, bool) and v:
            log.info(f"    ✅ {k}")
    log.info("")
    if apk_built:
        log.info(f"  APK output: {OUTPUT_DIR / 'app.apk'}")
        log.info("  Install on device: adb install output/app.apk")
    else:
        log.info("  Project ready for Android Studio:")
        log.info(f"    Open folder: {project_dir}")
        log.info("    Then: Build > Build Bundle(s)/APK(s) > Build APK(s)")
    log.info("")
    log.info(f"  Build log: {log_file}")
    log.info("═" * 62)
    log.info("  Developed by SANTHOSH A")
    log.info("═" * 62)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print(BANNER)

    html_path = INPUT_DIR / "index.html"

    # Validate input
    if not html_path.exists():
        log.error(f"Missing input file: {html_path}")
        log.error("Please place your index.html inside the /input_project/ folder.")
        sys.exit(1)

    log.info(f"Input HTML : {html_path}")
    log.info(f"Package    : {PACKAGE_NAME}")
    log.info(f"App Name   : {APP_NAME}")
    log.info("")

    # Step 1 – Analyze
    features = analyze_html(html_path)

    # Step 2/3 – Generate project
    project_dir = build_android_project(features, html_path)

    # Step 4 – Compile
    apk_built = compile_apk(project_dir)

    # Step 5 – Summary
    print_summary(features, project_dir, apk_built)


if __name__ == "__main__":
    main()
