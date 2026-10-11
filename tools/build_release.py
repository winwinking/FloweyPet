# -*- coding: utf-8 -*-
"""Build a release: PyInstaller -> verify -> zip -> SHA256 -> version.json draft.

Run from the repo root:  python tools/build_release.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(ROOT, "build")
DIST_DIR = os.path.join(ROOT, "dist")
APP_DIR = os.path.join(DIST_DIR, "FloweyPet")
RELEASE_DIR = os.path.join(ROOT, "release")

REQUIRED = [
    "FloweyPet.exe",
    "dialogue.json",
    "dialogues_anticheat.json",
    "dialogues_chase.json",
    "dialogues_flirt.json",
    "dialogues_game.json",
    "dialogues_info.json",
    "dialogues_spam.json",
    "dialogues_system.json",
    "stats_roast.json",
    "assets/icon.ico",
    "assets/fonts/fusion-pixel.ttf",
    "assets/fonts/FUSION-PIXEL-LICENSE.txt",
    "assets/font/DSEG-LICENSE.txt",
    "assets/faces_manifest.json",
    "assets/sfx/menu_move.wav",
]
FORBIDDEN_NAMES = {"config.json", "stats.json", "migration.json"}


def die(msg):
    print("错误：" + msg)
    sys.exit(1)


def read_version():
    with open(os.path.join(ROOT, "src", "version.py"), encoding="utf-8") as f:
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        die("src/version.py 里找不到 VERSION")
    return m.group(1)


def warn_if_dirty():
    r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    if lines:
        print("警告：工作区有未提交的改动（继续打包）：")
        for line in lines:
            print("  " + line)


def run_pyinstaller():
    for d in (BUILD_DIR, DIST_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d)
    cmd = [sys.executable, "-m", "PyInstaller", "FloweyPet.spec",
           "--noconfirm", "--clean"]
    print("运行：" + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        die("PyInstaller 失败，返回码 %d" % r.returncode)


def verify_contents():
    if not os.path.isdir(APP_DIR):
        die("没有生成 " + APP_DIR)
    missing = [p for p in REQUIRED if not os.path.isfile(os.path.join(APP_DIR, *p.split("/")))]
    if missing:
        die("dist/FloweyPet 缺少文件：\n  " + "\n  ".join(missing))
    bad = []
    for d, dirs, files in os.walk(APP_DIR):
        for n in dirs:
            if n == "dev_data":
                bad.append(os.path.relpath(os.path.join(d, n), APP_DIR))
        for n in files:
            if n in FORBIDDEN_NAMES or n.lower().endswith(".log") or ".bad-" in n:
                bad.append(os.path.relpath(os.path.join(d, n), APP_DIR))
    if bad:
        die("dist/FloweyPet 里出现了不该打包的文件：\n  " + "\n  ".join(bad))
    print("校验通过：必需文件齐全，没有用户数据/日志/坏文件")


def count_files():
    return sum(len(files) for _, _, files in os.walk(APP_DIR))


def make_zip(version):
    for name in ("README.md", "LICENSE"):
        shutil.copy2(os.path.join(ROOT, name), os.path.join(APP_DIR, name))
    os.makedirs(RELEASE_DIR, exist_ok=True)
    top = "FloweyPet-v%s" % version
    zip_name = top + ".zip"
    zip_path = os.path.join(RELEASE_DIR, zip_name)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for d, _, files in os.walk(APP_DIR):
            for n in sorted(files):
                full = os.path.join(d, n)
                rel = os.path.relpath(full, APP_DIR).replace("\\", "/")
                z.write(full, top + "/" + rel)
    return zip_path, zip_name


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    os.chdir(ROOT)
    version = read_version()
    print("版本：" + version)
    warn_if_dirty()
    run_pyinstaller()
    verify_contents()
    zip_path, zip_name = make_zip(version)
    digest = sha256_of(zip_path)
    with open(os.path.join(RELEASE_DIR, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("%s  %s\n" % (digest, zip_name))
    draft = {
        "version": version,
        "url": "https://github.com/winwinking/FloweyPet/releases/tag/v%s" % version,
        "line": "",
        "face": "neutral",
    }
    with open(os.path.join(RELEASE_DIR, "version.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("zip 路径：" + zip_path)
    print("大小：%.2f MB" % (os.path.getsize(zip_path) / 1024 / 1024))
    print("SHA256：" + digest)
    print("dist/FloweyPet 文件总数：%d" % count_files())


if __name__ == "__main__":
    main()
