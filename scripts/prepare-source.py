#!/usr/bin/env python3
"""Install the WF3000A Linux 6.6 board port into the pinned source tree.

精简版 —— 相对原版只做三件事（原版做四件）：
  1) 复制 DTS 到 target/linux/mediatek/dts/
  2) 把 device.mk 追加到 target/linux/mediatek/image/filogic.mk
  3) 在 02_network 里挂 360T7 的网口分组 + 写入硬编码 MAC
  （不再碰 platform.sh —— 不需要 sysupgrade，直接在 U-Boot Web 里刷固件）

相对原版的改动：

  [布局] 248MiB -> 114MiB
     末尾断言 248320 -> 116736，期望 0x0F800000 -> 0x07780000。
     原版不改这里会在「DTS 已复制、mk 已追加之后」才 AssertionError 崩溃。

  [MAC] 改为硬编码（原版逻辑对这台机器是错的）
     实测：factory 0x4 = ...:F7，factory 0xa = ...:F8，机身标签 = ...:F5
     本机分配块 = F5:LAN / F6:WAN / F7:2.4G / F8:5G
     原版 else 分支：LAN = F7-1 = F6（≠标签 F5），WAN = F7+1 = F8（与 5G 撞车）
     原版 if  分支：读 u-boot-env 的 "mac" key，但本机只有 "ethaddr"，
                     且 ethaddr=8e:61:1e:f5:ac:b6 首字节 0x8E 的 U/L 位 = 1，
                     是本地管理地址（软件随机生成），不可用。

  [容错] 02_network 的补丁改为「失败只警告、不中断」
     原版用精确正则定位 qihoo,360t7，上游一改就 RuntimeError 崩掉。
     现在打不上时只警告并继续 —— DTS 里的 nvmem(0x4 -> F7) 会兜底，
     MAC 至少是本机合法值，不会退化成随机。这换来更高的编译成功率。
"""

from pathlib import Path
import re
import shutil
import sys

# 114MiB 布局（对应 hanwckf 系 U-Boot 的 expand(114m)）
MTDPARTS_KIB = (1024, 512, 2048, 2048, 116736)
MTDPARTS_TOTAL = 0x07780000
MTDPARTS_STR = "mtdparts=nmbm0:1024k(bl2),512k(u-boot-env),2048k(factory),2048k(fip),116736k(ubi)"

# 机身实测 MAC（与机器背面标签一致）
LAN_MAC = "48:da:35:50:70:f5"
WAN_MAC = "48:da:35:50:70:f6"


def replace_once(path: Path, pattern: str, replacement: str) -> bool:
    """Replace the first match. Return True on success, False if not found."""
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare-source.py OPENWRT_SOURCE_DIR")

    source = Path(sys.argv[1]).resolve()
    project = Path(__file__).resolve().parents[1]
    custom = project / "custom" / "wf3000a"

    dts_target = source / "target/linux/mediatek/dts/mt7981b-kst-wf3000a.dts"
    image_makefile = source / "target/linux/mediatek/image/filogic.mk"
    network_file = source / "target/linux/mediatek/filogic/base-files/etc/board.d/02_network"

    if not image_makefile.is_file():
        raise FileNotFoundError(image_makefile)

    # ── 1) DTS ────────────────────────────────────────────
    shutil.copyfile(custom / dts_target.name, dts_target)

    # ── 2) 镜像定义 ───────────────────────────────────────
    image_text = image_makefile.read_text(encoding="utf-8")
    if "define Device/kst_wf3000a" in image_text:
        raise RuntimeError("upstream already contains kst_wf3000a; review the port before building")
    fragment = (custom / "device.mk").read_text(encoding="utf-8")
    image_makefile.write_text(image_text.rstrip() + "\n" + fragment, encoding="utf-8", newline="\n")

    # ── 3) 02_network：网口分组 + 硬编码 MAC ──────────────
    # 可选步骤：打不上也不致命（DTS 的 nvmem 会兜底），所以失败只警告。
    if network_file.is_file():
        ok_group = replace_once(
            network_file,
            r"^(\s*)qihoo,360t7\|\\$",
            r"\1kst,wf3000a|\\\n\1qihoo,360t7|\\",
        )
        ok_mac = replace_once(
            network_file,
            r"^(\s*)qihoo,360t7\)$",
            r'''\1kst,wf3000a)
\1\tlan_mac="{lan}"
\1\twan_mac="{wan}"
\1\tlabel_mac=$lan_mac
\1\t;;
\1qihoo,360t7)'''.format(lan=LAN_MAC, wan=WAN_MAC),
        )
        if not (ok_group and ok_mac):
            print(
                "::warning::02_network 补丁未完全应用（上游格式可能已变化）。\n"
                "  网口顺序={0}  MAC硬编码={1}\n"
                "  不致命：DTS 里的 nvmem(factory 0x4 -> {2}) 会兜底，\n"
                "  MAC 会是本机合法值 ...:F7 而非随机。\n"
                "  若刷完发现网口顺序不对或想要标签上的 ...:F5，\n"
                "  可在 LuCI 里 网络->接口->编辑->高级设置->覆盖MAC地址 手动填。".format(
                    ok_group, ok_mac, "48:da:35:50:70:f7"
                )
            )
    else:
        print(f"::warning::{network_file} not found; skipping 02_network patch")

    # ── 4) 分区校验 ───────────────────────────────────────
    assert sum(MTDPARTS_KIB) * 1024 == MTDPARTS_TOTAL
    print("WF3000A board port installed (114MiB layout)")
    print(MTDPARTS_STR)
    print(f"LAN={LAN_MAC}  WAN={WAN_MAC}")


if __name__ == "__main__":
    main()
