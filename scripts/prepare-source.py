#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WF3000A 板级移植安装脚本（构建时打补丁）

═══════════════════════════════════════════════════════════════
设计前提
────────
immortalwrt-mt798x-6.6 源码仓库【保持原样，不做任何手动修改】。
所有移植改动都由本脚本在 CI 里、克隆完源码之后、编译之前完成。

好处：
  - 源码可以随意换 commit / 换分支，不用维护 fork
  - 上游更新直接改 SOURCE_COMMIT 即可
代价：
  - 依赖正则/锚点定位，上游改动文件格式时可能失配
    → 本脚本对致命项（DTS、机型注册、MAC）严格失败，
      对非致命项（sysupgrade）降级为警告，避免误判成编译错误。

用法
────
    python3 scripts/prepare-source.py <源码目录>

做四件事
────────
  1) 复制 DTS                    → target/linux/mediatek/dts/
  2) 追加镜像定义 device.mk      → target/linux/mediatek/image/filogic.mk
  3) 改 02_network               → 接口分组(并入) + MAC(独立分支)
  4) 改 platform.sh              → sysupgrade(独立分支)
  5) 打完补丁后逐条复核，缺哪项报哪项
"""

from pathlib import Path
import re
import shutil
import sys

# ══════════════════════════════════════════════════════════════
# 一、常量区：所有关键数字集中在这里，改一处即可
# ══════════════════════════════════════════════════════════════

# ── 分区布局：114MiB（对应 hanwckf 系 U-Boot 的 expand(114m)）──
# nmbm0:1024k(bl2),512k(u-boot-env),2048k(factory),2048k(fip),116736k(ubi)
# 原 Anser-y 版本是 248MiB(248320k)，这里我们改成 114MiB(116736k)。
MTDPARTS_KIB = (1024, 512, 2048, 2048, 116736)
MTDPARTS_TOTAL = 0x07780000                 # = sum(MTDPARTS_KIB) * 1024
MTDPARTS_STR = (
    "mtdparts=nmbm0:1024k(bl2),512k(u-boot-env),2048k(factory),"
    "2048k(fip),116736k(ubi)"
)

# DTS / device.mk 里必须出现的数值（用于事后复核）
UBI_REG = "reg = <0x0580000 0x07200000>;"   # ubi 分区: 起始 0x580000 + 长度 114MiB
IMAGE_SIZE = "IMAGE_SIZE := 115712k"        # = 116736k - 1MiB，留给 NMBM/UBI 开销

# ── MAC：机身实测，与背面标签一致 ──────────────────────────
# 实测结果：
#   factory 0x4  = 48:DA:35:50:70:F7
#   factory 0xa  = 48:DA:35:50:70:F8
#   机身标签     = 48:DA:35:50:70:F5
# 分配块推断：F5=LAN  F6=WAN  F7=2.4G  F8=5G
# factory 里只有两个无线 MAC，以太网 MAC 不在 flash → 只能硬编码。
LAN_MAC = "48:da:35:50:70:f5"
WAN_MAC = "48:da:35:50:70:f6"

# ── 锚点机型：补丁插入位置的参照物 ──────────────────────────
# 选 360T7 是因为它与 WF3000A 同为 MT7531 交换机 + 4 个千兆口，
# 且它的接口配置串与我们的 DTS 完全一致（见下）。
ANCHOR = "qihoo,360t7"

# ── 接口分组：并入 360T7 那一组（共用动作串）────────────────
# 共用的是这行纯数据，与我们 DTS 的 lan1/lan2/lan3/wan 一字不差：
#   ucidef_set_interfaces_lan_wan "lan1 lan2 lan3" wan
# 因为只是数据，共用完全等价，所以用「续行并入」最省事。
EXPECT_IFACE_ACTION = 'ucidef_set_interfaces_lan_wan "lan1 lan2 lan3" wan'

# ── 是否给 platform.sh 打补丁（False = 只保留 U-Boot Web 刷机）──
ENABLE_SYSUPGRADE = True

# ── sysupgrade 分支里是否显式声明三个 UBI 卷设置 ────────────
# 依据：DTS 里 UBI 分区 label = "ubi"；
#       device.mk 里 KERNEL_IN_UBI := 1 → 生成 kernel / rootfs 卷。
# 与 360T7 那组的值一致，显式写出来更明确。
SET_CI_PARTS = True


# ══════════════════════════════════════════════════════════════
# 二、通用工具：shell case 语句的定位与插入
# ══════════════════════════════════════════════════════════════

def find_function(lines, name):
    """定位 shell 函数 `name()` 的行范围。

    兼容两种写法：
        platform_do_upgrade() {      ← 花括号同行
        mediatek_setup_macs()        ← 花括号在下一行
        {
    返回 (起始行, 结束行)，结束行是顶格的那个 `}`。
    """
    pat = re.compile(rf"^{re.escape(name)}\s*\(\)")
    for i, ln in enumerate(lines):
        if pat.match(ln.rstrip()):
            j = i + 1
            while j < len(lines):
                if lines[j].rstrip() == "}":
                    return i, j
                j += 1
            return i, len(lines) - 1
    return None


def backtrack_group(lines, idx):
    """从 case pattern 组中的某一行回溯到该组的【第一行】。

    case 的一个 pattern 组形如：
        asus,rt-ax52|\            ← 续行，以 |\\ 结尾
        qihoo,360t7|\
        routerich,ax3000-ubootmod)   ← 组结束

    只要上一行以 `|\\` 结尾就继续上移，最终停在组首。

    ⚠️ 这是关键：插入点必须是【组首之前】。
       在续行链中间插入内容会打断链路，
       导致相邻机型（如 asus,rt-ax52）也被并进我们的分支。
    """
    i = idx
    while i > 0 and lines[i - 1].rstrip().endswith("|\\"):
        i -= 1
    return i


def find_anchor_groups(lines, lo, hi, anchor):
    """在 [lo, hi) 行范围内，找出 anchor 作为 case pattern 出现的所有组首行号。

    同时兼容两种行尾：
        qihoo,360t7)       单独成组
        qihoo,360t7|\\      续行形式
    """
    pat = re.compile(rf"^\s*{re.escape(anchor)}\s*(\)|\|\\)$")
    hits = [i for i in range(lo, hi) if pat.match(lines[i].rstrip())]
    # 去重：同一个组可能被多个 pattern 命中
    return sorted({backtrack_group(lines, i) for i in hits})


def insert_before(lines, starts, block):
    """在多个位置前插入 block。从下往上插，避免行号漂移。"""
    for s in sorted(starts, reverse=True):
        lines[s:s] = block
    return len(starts)


def indent_of(line):
    m = re.match(r"^(\s*)", line)
    return m.group(1) if m else ""


# ══════════════════════════════════════════════════════════════
# 三、四个补丁动作
# ══════════════════════════════════════════════════════════════

def patch_dts(source, custom, log):
    """动作 1：复制设备树。

    DTS 决定了：分区表、交换机端口、GPIO(LED/按键)、MAC nvmem 偏移、无线校准来源。
    写错分区 → 内核找不到 UBI 卷，起不来。
    """
    dst = source / "target/linux/mediatek/dts/mt7981b-kst-wf3000a.dts"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(custom / "mt7981b-kst-wf3000a.dts", dst)
    log("动作1", f"DTS 已复制 → {dst.relative_to(source)}")


def patch_image_mk(source, custom, log):
    """动作 2：把机型定义追加到 filogic.mk。

    这一步【最关键】——它让 config 符号 CONFIG_TARGET_mediatek_filogic_DEVICE_kst_wf3000a
    得以存在。没有它，.config 里写这个符号会被 make defconfig 静默丢弃。

    必须【追加】而不是覆盖，否则会丢掉上游所有其他机型。
    """
    mk = source / "target/linux/mediatek/image/filogic.mk"
    text = mk.read_text(encoding="utf-8")

    if "define Device/kst_wf3000a" in text:
        # 幂等保护：重复追加会导致 make 报重复定义
        raise RuntimeError(
            "filogic.mk 里已存在 kst_wf3000a 定义。"
            "若源码已内置该机型，请先核对它的分区布局是不是 114MiB 再决定是否跳过本步骤。"
        )

    fragment = (custom / "device.mk").read_text(encoding="utf-8")
    mk.write_text(text.rstrip("\n") + "\n\n" + fragment, encoding="utf-8", newline="\n")
    log("动作2", f"镜像定义已追加 → {mk.relative_to(source)}")


def patch_02_network(source, log):
    """动作 3：改网络配置脚本（两处，性质不同）。

    ── 3a 接口分组：并入 360T7（续行形式）──────────────────
    共用的是【纯数据】：ucidef_set_interfaces_lan_wan "lan1 lan2 lan3" wan
    与我们 DTS 的端口定义完全一致，共用等价，零风险。
    为什么必须加：默认分支 `*)` 给的是 "lan1 lan2 lan3 lan4" wan，
    引用了不存在的 lan4，会导致 LAN 桥接起不来。

    ── 3b MAC：独立分支（硬编码）──────────────────────────
    不能并入，因为 360T7 那行是【读取逻辑】：
        lan_mac=$(mtd_get_mac_ascii factory lanMac)
    我们的 factory 是二进制布局（0x4=F7 / 0xa=F8），没有 lanMac 这个 ASCII key，
    读出来是空值 → 不设置任何 MAC → 回退到内核值（就是原先 MAC 不对的问题）。
    所以必须自己写分支把值硬编码进去。
    """
    f = source / "target/linux/mediatek/filogic/base-files/etc/board.d/02_network"
    if not f.is_file():
        raise FileNotFoundError(f)

    lines = f.read_text(encoding="utf-8").split("\n")

    # ── 3a 接口分组 ──
    rng = find_function(lines, "mediatek_setup_interfaces")
    if not rng:
        raise RuntimeError("02_network 里找不到 mediatek_setup_interfaces()")
    lo, hi = rng
    starts = find_anchor_groups(lines, lo, hi, ANCHOR)
    if not starts:
        raise RuntimeError(
            f"mediatek_setup_interfaces() 里找不到锚点 {ANCHOR}，无法定位接口分组。"
            "上游文件可能已改版，请人工核对。"
        )
    ind = indent_of(lines[starts[0]])
    # 续行并入：只加一行 kst,wf3000a|\
    n_iface = insert_before(lines, starts, [f"{ind}kst,wf3000a|\\"])

    # ── 3b MAC 分支 ──
    # 注意：3a 插入后行号变了，必须重新定位函数
    lines = "\n".join(lines).split("\n")
    rng = find_function(lines, "mediatek_setup_macs")
    if not rng:
        raise RuntimeError("02_network 里找不到 mediatek_setup_macs()")
    lo, hi = rng
    starts = find_anchor_groups(lines, lo, hi, ANCHOR)
    if not starts:
        raise RuntimeError(
            f"mediatek_setup_macs() 里找不到锚点 {ANCHOR}，无法定位 MAC 分支位置。"
        )
    ind = indent_of(lines[starts[0]])
    # 独立分支：插在 360T7 组【之前】
    block = [
        f"{ind}kst,wf3000a)",
        f'{ind}\tlan_mac="{LAN_MAC}"',
        f'{ind}\twan_mac="{WAN_MAC}"',
        f"{ind}\tlabel_mac=$lan_mac",
        f"{ind}\t;;",
    ]
    n_mac = insert_before(lines, starts, block)

    f.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    log("动作3", f"02_network 已改：接口分组 {n_iface} 处 + MAC 分支 {n_mac} 处")


def patch_platform_sh(source, log):
    """动作 4：打通 sysupgrade。

    在 padavanonly 6.6 这份 platform.sh 里，360T7 只出现在 platform_do_upgrade()
    一个函数中；platform_check_image() 里没有机型分支，走通用 `*)` 的
    nand_do_platform_check，已覆盖我们，无需补丁。

    本脚本不写死"必须补几处"，而是数 ANCHOR 出现几次就补几次，
    这样上游改版也不会误判。
    """
    f = source / "target/linux/mediatek/filogic/base-files/lib/upgrade/platform.sh"
    if not f.is_file():
        raise FileNotFoundError(f)

    lines = f.read_text(encoding="utf-8").split("\n")

    # 所有出现过 ANCHOR 的函数都要补（通常是 do_upgrade 一个）
    targets = []
    for fname in ("platform_check_image", "platform_do_upgrade"):
        rng = find_function(lines, fname)
        if rng:
            lo, hi = rng
            starts = find_anchor_groups(lines, lo, hi, ANCHOR)
            if starts:
                targets.append((fname, starts))

    if not targets:
        # 非致命：只是没有 sysupgrade，U-Boot Web 刷机不受影响
        print("::warning::platform.sh 里未找到锚点，跳过 sysupgrade 补丁。"
              "仍可用 U-Boot Web 上传 factory.bin 刷机。")
        return 0

    total = 0
    for fname, starts in targets:
        ind = indent_of(lines[starts[0]])
        if ENABLE_SYSUPGRADE:
            block = [f"{ind}kst,wf3000a)"]
            if SET_CI_PARTS:
                block += [
                    f'{ind}\tCI_UBIPART="ubi"',
                    f'{ind}\tCI_KERNPART="kernel"',
                    f'{ind}\tCI_ROOTPART="rootfs"',
                ]
            block += [f'{ind}\tnand_do_upgrade "$1"', f"{ind}\t;;"]
            total += insert_before(lines, starts, block)

    f.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    log("动作4", f"platform.sh 已改：sysupgrade 分支 {total} 处")
    return total


# ══════════════════════════════════════════════════════════════
# 四、打完补丁后复核
# ══════════════════════════════════════════════════════════════

def verify(source):
    """逐条复核，缺哪项报哪项。致命项返回 False 让 workflow 失败。"""
    print("\n── 复核 ──────────────────────────────────────────")
    failed = []

    def chk(cond, name, detail=""):
        # 返回 cond，便于用 `if chk(...)` 做「父项通过才查子项」的嵌套
        if cond:
            print(f"  [OK]   {name}" + (f"  ({detail})" if detail else ""))
        else:
            failed.append(name)
            print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))
        return cond

    # 1) DTS
    dts = source / "target/linux/mediatek/dts/mt7981b-kst-wf3000a.dts"
    if chk(dts.is_file(), "DTS 存在"):
        t = dts.read_text(encoding="utf-8")
        chk(UBI_REG in t, "DTS 分区为 114MiB", UBI_REG)
        chk('compatible = "kst,wf3000a"' in t, "DTS compatible 正确(必须小写)")
        chk("mediatek,nmbm" in t, "NMBM 已启用")

    # 2) filogic.mk
    mk = source / "target/linux/mediatek/image/filogic.mk"
    t = mk.read_text(encoding="utf-8") if mk.is_file() else ""
    chk("define Device/kst_wf3000a" in t, "机型已注册")
    chk(IMAGE_SIZE in t, "IMAGE_SIZE = 115712k")
    chk("TARGET_DEVICES += kst_wf3000a" in t, "TARGET_DEVICES 已追加")

    # 3) 02_network
    f = source / "target/linux/mediatek/filogic/base-files/etc/board.d/02_network"
    t = f.read_text(encoding="utf-8") if f.is_file() else ""
    chk("kst,wf3000a|\\" in t, "接口分组已并入(续行)")
    chk(EXPECT_IFACE_ACTION in t, "接口动作串正确", EXPECT_IFACE_ACTION)
    chk(LAN_MAC in t and WAN_MAC in t, "硬编码 MAC 已写入", f"{LAN_MAC} / {WAN_MAC}")

    # 4) platform.sh（非致命）
    f = source / "target/linux/mediatek/filogic/base-files/lib/upgrade/platform.sh"
    t = f.read_text(encoding="utf-8") if f.is_file() else ""
    if "kst,wf3000a" in t:
        print("  [OK]   sysupgrade 分支已写入")
    else:
        print("  [warn] sysupgrade 未打通（不影响 U-Boot Web 刷机）")

    # 5) 算术
    total = sum(MTDPARTS_KIB) * 1024
    chk(total == MTDPARTS_TOTAL, "分区和 == 总地址空间",
        f"{sum(MTDPARTS_KIB)} KiB -> {hex(total)}")
    chk(MTDPARTS_KIB[-1] - 1024 == 115712, "IMAGE_SIZE == ubi - 1MiB")

    print("──────────────────────────────────────────────────")
    if failed:
        print(f"::error:: {len(failed)} 项未通过: {', '.join(failed)}")
        return False
    print("复核全部通过")
    return True


# ══════════════════════════════════════════════════════════════
# 五、主流程
# ══════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare-source.py OPENWRT_SOURCE_DIR")

    source = Path(sys.argv[1]).resolve()
    custom = Path(__file__).resolve().parents[1] / "custom" / "wf3000a"

    if not source.is_dir():
        raise SystemExit(f"源码目录不存在: {source}")

    print("=" * 66)
    print("WF3000A 板级移植 —— 构建时打补丁")
    print(f"源码: {source}")
    print("=" * 66)

    def log(tag, msg):
        print(f"  {tag}: {msg}")

    patch_dts(source, custom, log)
    patch_image_mk(source, custom, log)
    patch_02_network(source, log)
    if ENABLE_SYSUPGRADE:
        patch_platform_sh(source, log)
    else:
        print("  （ENABLE_SYSUPGRADE=False，跳过 platform.sh 补丁）")

    print(f"\n布局: {MTDPARTS_STR}")
    print(f"MAC : LAN={LAN_MAC}  WAN={WAN_MAC}   (F5/F6/F7/F8 = LAN/WAN/2.4G/5G)")

    if not verify(source):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
