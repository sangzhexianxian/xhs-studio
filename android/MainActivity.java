package io.github.xhsstudio;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.text.InputType;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

import org.json.JSONArray;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * xhs-studio 安卓壳。
 *
 * 它只干一件网页干不了的事：把（可能跨域的）图片批量写进系统相册。
 * 网页版受浏览器安全模型限制，做不到静默写相册；这里用 MediaStore 做，
 * Android 10+ 走 RELATIVE_PATH + IS_PENDING，不需要存储权限。
 *
 * 服务器地址不写死：首次启动弹框输入，存在 SharedPreferences 里，
 * 之后长按页面顶部 3 秒可以再次修改。
 */
public class MainActivity extends Activity {

    private static final String PREF = "xhs";
    private static final String KEY_HOME = "home";
    private static final String DEFAULT_ALBUM = "我的图文";

    private WebView wv;
    private String home;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);

        // API 23~28 写相册需要存储权限；29+ 用 MediaStore 不需要
        if (Build.VERSION.SDK_INT >= 23 && Build.VERSION.SDK_INT < 29) {
            if (checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE)
                    != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{android.Manifest.permission.WRITE_EXTERNAL_STORAGE}, 1);
            }
        }

        wv = new WebView(this);
        WebSettings s = wv.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        wv.setWebViewClient(new WebViewClient());
        wv.addJavascriptInterface(new Bridge(), "AndroidBridge");
        setContentView(wv);

        home = prefs().getString(KEY_HOME, "");
        if (home == null || home.trim().length() == 0) {
            askHome();
        } else {
            wv.loadUrl(home);
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREF, MODE_PRIVATE);
    }

    /** 首次启动 / 长按改地址 */
    private void askHome() {
        final EditText et = new EditText(this);
        et.setHint("http://你的服务器地址/xhs/");
        et.setText(home == null ? "" : home);
        et.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        ViewGroup.MarginLayoutParams lp = new ViewGroup.MarginLayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        int pad = (int) (getResources().getDisplayMetrics().density * 16);
        et.setLayoutParams(lp);
        et.setPadding(pad, pad, pad, pad);

        new AlertDialog.Builder(this)
                .setTitle("发布页地址")
                .setMessage("填你自己部署的发布页地址，末尾带斜杠。\n例：http://192.168.1.10:8000/xhs/")
                .setView(et)
                .setCancelable(false)
                .setPositiveButton("打开", (d, w) -> {
                    String v = et.getText().toString().trim();
                    if (v.length() == 0) {
                        finish();
                        return;
                    }
                    if (!v.startsWith("http")) {
                        v = "http://" + v;
                    }
                    if (!v.endsWith("/")) {
                        v = v + "/";
                    }
                    home = v;
                    prefs().edit().putString(KEY_HOME, v).apply();
                    wv.loadUrl(v);
                })
                .setNegativeButton("退出", (d, w) -> finish())
                .show();
    }

    @Override
    public void onBackPressed() {
        if (wv != null && wv.canGoBack()) {
            wv.goBack();
            return;
        }
        super.onBackPressed();
    }

    private void js(final String code) {
        runOnUiThread(() -> {
            try {
                wv.evaluateJavascript(code, null);
            } catch (Throwable ignored) {
            }
        });
    }

    private void tip(final String m) {
        runOnUiThread(() -> Toast.makeText(MainActivity.this, m, Toast.LENGTH_SHORT).show());
    }

    class Bridge {

        @JavascriptInterface
        public String isNative() {
            return "1";
        }

        /** 记住地址，方便网页端做"修改服务器"入口 */
        @JavascriptInterface
        public String getHome() {
            return home == null ? "" : home;
        }

        @JavascriptInterface
        public void resetHome() {
            runOnUiThread(() -> askHome());
        }

        /**
         * 批量存图。
         *
         * @param urlsJson 形如 ["http://.../01.jpg", ...]
         * @param album    相册里的子目录名，留空用默认
         */
        @JavascriptInterface
        public void saveImages(String urlsJson, String album) {
            final String[] urls;
            try {
                JSONArray a = new JSONArray(urlsJson);
                urls = new String[a.length()];
                for (int i = 0; i < a.length(); i++) {
                    urls[i] = a.optString(i, "");
                }
            } catch (Throwable t) {
                tip("参数错误");
                return;
            }
            final String dir = (album == null || album.trim().length() == 0)
                    ? DEFAULT_ALBUM : album.trim();

            new Thread(() -> {
                int ok = 0, fail = 0;
                for (int i = 0; i < urls.length; i++) {
                    String u = urls[i];
                    if (u == null || u.length() == 0) {
                        continue;
                    }
                    String name = u.substring(u.lastIndexOf('/') + 1);
                    int q = name.indexOf('?');
                    if (q > 0) {
                        name = name.substring(0, q);
                    }
                    if (name.length() == 0) {
                        name = "img_" + (i + 1) + ".jpg";
                    }
                    try {
                        byte[] data = fetch(u);
                        if (data != null && save(data, name, dir)) {
                            ok++;
                        } else {
                            fail++;
                        }
                    } catch (Throwable t) {
                        fail++;
                    }
                    js("window.__saveProgress&&window.__saveProgress(" + (i + 1) + "," + urls.length + ")");
                }
                tip("已存入相册 " + ok + " 张" + (fail > 0 ? "，失败 " + fail : ""));
                js("window.__saveDone&&window.__saveDone(" + ok + "," + fail + ")");
            }).start();
        }
    }

    private byte[] fetch(String u) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(u).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(30000);
        c.setRequestProperty("User-Agent", "Mozilla/5.0");
        c.setRequestProperty("Accept", "image/*");
        InputStream in = c.getInputStream();
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0) {
            bo.write(buf, 0, n);
        }
        in.close();
        c.disconnect();
        return bo.toByteArray();
    }

    private boolean save(byte[] data, String name, String dir) {
        try {
            ContentValues cv = new ContentValues();
            cv.put(MediaStore.Images.Media.DISPLAY_NAME, name);
            cv.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
            if (Build.VERSION.SDK_INT >= 29) {
                cv.put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/" + dir);
                cv.put(MediaStore.Images.Media.IS_PENDING, 1);
            }
            Uri uri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
            if (uri == null) {
                return false;
            }
            OutputStream os = getContentResolver().openOutputStream(uri);
            if (os == null) {
                return false;
            }
            os.write(data);
            os.flush();
            os.close();
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues up = new ContentValues();
                up.put(MediaStore.Images.Media.IS_PENDING, 0);
                getContentResolver().update(uri, up, null, null);
            }
            return true;
        } catch (Throwable t) {
            return false;
        }
    }
}
