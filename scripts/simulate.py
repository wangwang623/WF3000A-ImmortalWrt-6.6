#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地模拟运行 —— 不需要 GitHub Actions、不需要真的编译。

模拟内容：
  1) 建一棵「未打补丁」的模拟源码树（用真实上游文件内容）
  2) 跑 prepare-source.py 打补丁
  3) 校验打完补丁的 shell 文件语法（sh -n）
  4) 模拟 make defconfig 的符号解析 + workflow 的硬/软校验
  5) 校验 workflow(yaml) / 脚本(python) 语法
  6) 额外跑两个「异常场景」，确认失败能被正确拦住

用法：
    python3 scripts/simulate.py
"""

import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS, FAIL = [], []


def step(name):
    print(f"\n{'='*68}\n▶ {name}\n{'='*68}")


def chk(cond, name, detail=""):
    tag = "OK  " if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f"  ({detail})" if detail else ""))
    (PASS if cond else FAIL).append(name)
    return cond


def run(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                          text=True, **kw)


# ══════════════════════════════════════════════════════════════
# 1. 静态语法校验
# ══════════════════════════════════════════════════════════════
step("1. 静态语法校验")

r = run([sys.executable, "-m", "py_compile", str(ROOT / "scripts/prepare-source.py")])
chk(r.returncode == 0, "prepare-source.py 语法正确", r.stderr.strip()[:80])

try:
    import yaml
    yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    chk(True, "build.yml YAML 正确")
except Exception as e:
    chk(False, "build.yml YAML 正确", str(e))

# 这两个是【参考文件】：云编译不读它们（prepare-source.py 直接在源码上做插入），
# 只有本模拟脚本会读（用来反推「未打补丁」的上游版本做对照测试）。
# 缺失时给出清晰提示而非 traceback —— 云编译不受影响。
missing_ref = [f for f in ("02_network", "platform.sh")
               if not (ROOT / "custom/wf3000a" / f).is_file()]
if missing_ref:
    print(f"  [提示] 参考文件缺失: {', '.join(missing_ref)}")
    print("         云编译【不需要】它们，仅本模拟脚本需要。")
    print("         若已自行删除，请跳过本模拟；或把它们放回 custom/wf3000a/ 。")
    sys.exit(0)

for f in ("02_network", "platform.sh"):
    r = run(["sh", "-n", str(ROOT / "custom/wf3000a" / f)])
    chk(r.returncode == 0, f"{f} (参考文件) shell 语法正确")

# config 文件格式自检
cfg_lines = (ROOT / "config/wf3000a.config").read_text().split("\n")
syms = [l.split("=")[0] for l in cfg_lines
        if l.startswith("CONFIG_") and not l.startswith("#")]
dup = {s for s in syms if syms.count(s) > 1}
chk(not dup, "config 无重复符号", str(dup) if dup else "")
bad = [l for l in cfg_lines
       if l.strip() and not l.startswith("#") and not l.startswith("CONFIG_")]
chk(not bad, "config 无非法行", str(bad) if bad else "")


# ══════════════════════════════════════════════════════════════
# 2. 建模拟源码树 + 打补丁
# ══════════════════════════════════════════════════════════════
step("2. 建模拟源码树并运行 prepare-source.py")

tmp = Path(tempfile.mkdtemp(prefix="wf3000a-sim-"))
src = tmp / "openwrt"
S = src / "target/linux/mediatek"
(S / "dts").mkdir(parents=True)
(S / "image").mkdir(parents=True)
(S / "filogic/base-files/etc/board.d").mkdir(parents=True)
(S / "filogic/base-files/lib/upgrade").mkdir(parents=True)

# 用「打过补丁的参考文件」反推出未打补丁的上游版本
def strip_patched(name):
    lines = (ROOT / "custom/wf3000a" / name).read_text().split("\n")
    out, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "kst,wf3000a|\\":          # 续行并入
            i += 1
            continue
        if s == "kst,wf3000a)":            # 独立分支
            while i < len(lines) and lines[i].strip() != ";;":
                i += 1
            i += 1
            continue
        out.append(lines[i]); i += 1
    return "\n".join(out)

(S / "filogic/base-files/etc/board.d/02_network").write_text(strip_patched("02_network"))
(S / "filogic/base-files/lib/upgrade/platform.sh").write_text(strip_patched("platform.sh"))
(S / "image/filogic.mk").write_text(
    "define Device/qihoo_360t7\n  DEVICE_VENDOR := Qihoo\nendef\n"
    "TARGET_DEVICES += qihoo_360t7\n"
)
print(f"  模拟源码树: {src}")

r = run([sys.executable, str(ROOT / "scripts/prepare-source.py"), str(src)])
print("\n".join("    " + l for l in r.stdout.strip().split("\n")))
chk(r.returncode == 0, "prepare-source.py 运行成功")

# 打完补丁后的 shell 语法
for rel in ("filogic/base-files/etc/board.d/02_network",
            "filogic/base-files/lib/upgrade/platform.sh"):
    r = run(["sh", "-n", str(S / rel)])
    chk(r.returncode == 0, f"打补丁后 {Path(rel).name} 语法正确", r.stderr.strip()[:60])

# 补丁位置正确性
net = (S / "filogic/base-files/etc/board.d/02_network").read_text()
chk('kst,wf3000a|\\' in net, "接口分组已并入（续行）")
chk('ucidef_set_interfaces_lan_wan "lan1 lan2 lan3" wan' in net, "接口动作串正确")
chk('lan_mac="48:da:35:50:70:f5"' in net, "LAN MAC 已写入")
chk('wan_mac="48:da:35:50:70:f6"' in net, "WAN MAC 已写入")

plat = (S / "filogic/base-files/lib/upgrade/platform.sh").read_text()
chk("kst,wf3000a)" in plat, "sysupgrade 分支已写入")
chk('CI_UBIPART="ubi"' in plat and 'nand_do_upgrade "$1"' in plat, "sysupgrade 动作正确")

# 续行链未被打断：kst,wf3000a|\ 的下一行应仍以 |\ 或 ) 结尾
lines = net.split("\n")
idx = next(i for i, l in enumerate(lines) if l.strip() == "kst,wf3000a|\\")
nxt = lines[idx + 1].strip()
chk(nxt.endswith("|\\") or nxt.endswith(")"), "续行链完整未被打断", nxt[:40])


# ══════════════════════════════════════════════════════════════
# 3. 模拟 make defconfig + workflow 校验
# ══════════════════════════════════════════════════════════════
step("3. 模拟 make defconfig 与 workflow 校验")

# 模拟 feeds install 后可用的包（真实存在的包名）
# 注意：不含 libustream-*，因为 config 里已不再写它，
#      由 opkg 的依赖自动 select。这里正好验证「不写也能过软校验」。
AVAILABLE = {
    "luci", "opkg", "ca-bundle", "ca-certificates", "luci-i18n-base-zh-cn",
    "libustream-openssl", "ppp", "ppp-mod-pppoe", "kmod-nft-offload",
    "luci-app-firewall", "luci-i18n-firewall-zh-cn",
    "luci-app-package-manager", "luci-i18n-package-manager-zh-cn",
    # 注意：不含旧名 luci-app-opkg / luci-i18n-opkg-zh-cn，
    #       因为 24.10 已改名。这里正好验证「只留新名也能通过」。
    "kmod-mediatek_hnat", "kmod-mt7981-firmware", "mt7981-wo-firmware",
    "uhttpd", "rpcd", "libuci", "libubus",
}
# 机型符号：由 device.mk 的 TARGET_DEVICES 生成（第 2 步已追加）
DEVICE_SYMBOLS = {"kst_wf3000a", "qihoo_360t7"}

REQUIRED = [
    "CONFIG_TARGET_mediatek_filogic_DEVICE_kst_wf3000a",
    "CONFIG_PACKAGE_kmod-mediatek_hnat",
    "CONFIG_PACKAGE_luci",
    "CONFIG_LUCI_LANG_zh_Hans",          # 中文界面：明确要求，缺失即中止
]

# 「新旧双名」组：两个名字只要【至少一个】存在即通过，两个都缺才失败。
# 用于 luci-app-package-manager / luci-app-opkg 这种改过名的包。
RENAME_GROUPS = [
    ("LuCI 软件包页面", ["CONFIG_PACKAGE_luci-app-package-manager",
                          "CONFIG_PACKAGE_luci-app-opkg"]),
    ("软件包页面中文化",  ["CONFIG_PACKAGE_luci-i18n-package-manager-zh-cn",
                          "CONFIG_PACKAGE_luci-i18n-opkg-zh-cn"]),
]
SOFT = [
    "CONFIG_PACKAGE_opkg",
    "CONFIG_PACKAGE_ca-bundle",
    "CONFIG_PACKAGE_ca-certificates",
]


def defconfig(raw_symbols, available, devices):
    """模拟 make defconfig：符号不存在就静默丢弃。"""
    kept = []
    for s in raw_symbols:
        if s.startswith("CONFIG_PACKAGE_"):
            pkg = s[len("CONFIG_PACKAGE_"):]
            if pkg in available:
                kept.append(s)
            # else: 静默丢弃（这正是真实行为）
        elif "_DEVICE_" in s:
            dev = s.split("_DEVICE_")[1]
            if dev in devices:
                kept.append(s)
        else:
            kept.append(s)
    return set(kept)


def validate(kept, available, devices, label):
    print(f"\n  ── 场景：{label} ──")
    ok = True
    for sym in REQUIRED:
        if sym in kept:
            print(f"    [OK  ] 硬校验 {sym}")
        else:
            print(f"    [FAIL] 硬校验 {sym}  ← 编译将中止")
            ok = False
    # 新旧双名组：至少一个存在即通过
    for gname, group in RENAME_GROUPS:
        hit = [s for s in group if s in kept]
        if hit:
            print(f"    [OK  ] 双名组 {gname}  → {hit[0]}")
        else:
            print(f"    [FAIL] 双名组 {gname}  ← 两个名字都没有，编译将中止")
            ok = False
    for sym in SOFT:
        if sym in kept:
            print(f"    [OK  ] 软校验 {sym}")
        else:
            print(f"    [warn] 软校验 {sym}  ← 仅提醒，不中止编译")
    return ok


# 读取 config 里所有 =y 的符号（注意要去掉 "=y" 后缀，只留符号名）
raw = [l.strip().split("=")[0] for l in cfg_lines
       if l.startswith("CONFIG_") and l.endswith("=y")]
print(f"  config 中待解析符号 {len(raw)} 个")

# 场景 A：一切正常
kept_a = defconfig(raw, AVAILABLE, DEVICE_SYMBOLS)
chk(validate(kept_a, AVAILABLE, DEVICE_SYMBOLS, "A 全部包可用"), "场景A 硬校验通过")

# 场景 B：ca-certificates 被上游改名（模拟静默丢弃）
avail_b = AVAILABLE - {"ca-certificates"}
kept_b = defconfig(raw, avail_b, DEVICE_SYMBOLS)
ok_b = validate(kept_b, avail_b, DEVICE_SYMBOLS, "B ca-certificates 缺失")
chk(ok_b, "场景B 硬校验仍通过（软项缺失不误伤编译）")

# 场景 C：device.mk 没追加（机型符号不存在）—— 必须失败
kept_c = defconfig(raw, AVAILABLE, {"qihoo_360t7"})
ok_c = validate(kept_c, AVAILABLE, {"qihoo_360t7"}, "C device.mk 未追加")
chk(not ok_c, "场景C 正确拦截（机型符号丢失时中止）")

# 场景 D：中文语言符号被静默丢弃 —— 必须失败（用户明确要求中文界面）
raw_d = [s for s in raw if s != "CONFIG_LUCI_LANG_zh_Hans"]
kept_d = defconfig(raw_d, AVAILABLE, DEVICE_SYMBOLS)
ok_d = validate(kept_d, AVAILABLE, DEVICE_SYMBOLS, "D 中文符号丢失")
chk(not ok_d, "场景D 正确拦截（中文界面缺失时中止）")

# 场景 D2：i18n 包名对不上被丢弃 —— 不应失败（它只是兜底）
avail_d2 = AVAILABLE - {"luci-i18n-base-zh-cn"}
kept_d2 = defconfig(raw, avail_d2, DEVICE_SYMBOLS)
ok_d2 = validate(kept_d2, avail_d2, DEVICE_SYMBOLS, "D2 兜底 i18n 包缺失")
chk(ok_d2, "场景D2 兜底包缺失不误伤（主要靠 LUCI_LANG 保证）")

# 场景 E：源码里只有【旧名】luci-app-opkg（24.10 之前）—— 应通过
avail_e = (AVAILABLE - {"luci-app-package-manager",
                        "luci-i18n-package-manager-zh-cn"}) | {
    "luci-app-opkg", "luci-i18n-opkg-zh-cn"}
kept_e = defconfig(raw, avail_e, DEVICE_SYMBOLS)
ok_e = validate(kept_e, avail_e, DEVICE_SYMBOLS, "E 源码只有旧名 opkg")
chk(ok_e, "场景E 旧名可用时通过（双名保险生效）")

# 场景 E2：新旧名字【都没有】—— 必须失败
avail_e2 = AVAILABLE - {"luci-app-package-manager",
                        "luci-i18n-package-manager-zh-cn"}
kept_e2 = defconfig(raw, avail_e2, DEVICE_SYMBOLS)
ok_e2 = validate(kept_e2, avail_e2, DEVICE_SYMBOLS, "E2 新旧名都缺失")
chk(not ok_e2, "场景E2 正确拦截（软件包页面彻底缺失时中止）")

# 场景 F：kmod-nft-offload / ppp 等新增项缺失 —— 只警告不中止（软项）
avail_f = AVAILABLE - {"kmod-nft-offload", "ppp-mod-pppoe",
                       "libustream-openssl", "luci-app-firewall"}
kept_f = defconfig(raw, avail_f, DEVICE_SYMBOLS)
ok_f = validate(kept_f, avail_f, DEVICE_SYMBOLS, "F 新增可选项缺失")
chk(ok_f, "场景F 新增项缺失不误伤编译（静默丢弃，仅影响体验）")


# ══════════════════════════════════════════════════════════════
# 4. 幂等性：重复运行应报错而非生成坏固件
# ══════════════════════════════════════════════════════════════
step("4. 幂等性检查（重复运行）")
r = run([sys.executable, str(ROOT / "scripts/prepare-source.py"), str(src)])
chk(r.returncode != 0 and "已存在" in r.stderr,
    "重复运行时明确报错停止", r.stderr.strip().split("\n")[-1][:70])


# ══════════════════════════════════════════════════════════════
# 5. 汇总
# ══════════════════════════════════════════════════════════════
shutil.rmtree(tmp, ignore_errors=True)
shutil.rmtree(ROOT / "scripts/__pycache__", ignore_errors=True)

step("汇总")
print(f"  通过 {len(PASS)} 项")
if FAIL:
    print(f"  失败 {len(FAIL)} 项:")
    for f in FAIL:
        print(f"    - {f}")
    sys.exit(1)
print("  ✅ 全部通过")
