#!/bin/bash
# 构建 xhs-studio 安卓壳（不依赖 Gradle，只要 Android SDK 的 build-tools + platform）
#
# 用法：
#   ANDROID_SDK=$HOME/Android/Sdk bash build.sh
# 可选：
#   PKG=io.github.xhsstudio     包名
#   KEYSTORE=my.keystore        签名文件（不存在则自动生成）
#   APPNAME="图文发布"           应用名
set -e

SDK="${ANDROID_SDK:-${ANDROID_HOME:-$HOME/Android/Sdk}}"
APPNAME="${APPNAME:-图文发布}"
PKG="${PKG:-io.github.xhsstudio}"
OUT="${OUT:-xhs-post.apk}"

[ -d "$SDK" ] || { echo "[err] 找不到 Android SDK：$SDK"; echo "      装 SDK 后设置 ANDROID_SDK=<路径>"; exit 1; }

BT=$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1)
PLATDIR=$(ls -d "$SDK"/platforms/android-* 2>/dev/null | sort -V | tail -1)
[ -n "$BT" ] || { echo "[err] $SDK/build-tools 下没有 build-tools"; exit 1; }
[ -n "$PLATDIR" ] || { echo "[err] $SDK/platforms 下没有 android-XX"; exit 1; }
PLAT="$PLATDIR/android.jar"

# JDK
if [ -z "$JAVA_HOME" ]; then
  for j in /usr/lib/jvm/java-17-openjdk-amd64 /usr/lib/jvm/java-11-openjdk-amd64 /Library/Java/JavaVirtualMachines/*/Contents/Home; do
    [ -d "$j" ] && JAVA_HOME="$j" && break
  done
fi
[ -n "$JAVA_HOME" ] || { echo "[err] 没找到 JDK 17（javac 需要）"; exit 1; }
export JAVA_HOME

# 签名
KEY="${KEYSTORE:-debug.keystore}"
if [ ! -f "$KEY" ]; then
  echo "[0/6] 生成调试签名 $KEY"
  keytool -genkeypair -v -keystore "$KEY" -storepass android -keypass android \
    -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US" >/dev/null
fi

# 应用名写进 strings.xml
sed -i.bak "s|<string name=\"app_name\">.*</string>|<string name=\"app_name\">${APPNAME}</string>|" res/values/strings.xml && rm -f res/values/strings.xml.bak

echo "[1/6] 编译资源"
rm -rf gen classes.dex app-unsigned.apk app-aligned.apk "$OUT" res.zip
mkdir -p gen
"$BT/aapt2" compile --dir res -o res.zip

echo "[2/6] 链接资源"
"$BT/aapt2" link -o app-unsigned.apk -I "$PLAT" \
  --manifest AndroidManifest.xml --java gen \
  --min-sdk-version 24 --target-sdk-version 33 res.zip

echo "[3/6] javac"
javac -encoding UTF-8 --release 11 -cp "$PLAT" -d gen/classes \
  "gen/${PKG//.//}/R.java" MainActivity.java

echo "[4/6] d8"
"$BT/d8" --lib "$PLAT" --min-api 24 --output . "gen/classes/${PKG//.//}"/*.class

echo "[5/6] 打包 + 对齐"
zip -j app-unsigned.apk classes.dex >/dev/null
"$BT/zipalign" -f 4 app-unsigned.apk app-aligned.apk

echo "[6/6] 签名"
"$BT/apksigner" sign --ks "$KEY" --ks-pass pass:android --key-pass pass:android \
  --ks-key-alias androiddebugkey --out "$OUT" app-aligned.apk

ls -la "$OUT"
echo "BUILD DONE → $OUT"
