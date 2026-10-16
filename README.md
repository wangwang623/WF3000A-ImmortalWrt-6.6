# WF3000A ImmortalWrt 6.6 云编译工程

本工程使用 [`padavanonly/immortalwrt-mt798x-6.6`](https://github.com/padavanonly/immortalwrt-mt798x-6.6) 的 `openwrt-24.10-6.6` 分支，通过 GitHub Actions 编译 KST-WF3000A 固件。

上游 6.6 分支目前没有 WF3000A 设备定义。本工程把旧版 WF3000A 板级描述移植到 `mediatek/filogic` 目标，并固定源码提交，避免上游变化导致补丁静默错位。

## 分区布局

内核 DTS 使用以下 NMBM 分区表：

```text
mtdparts=nmbm0:1024k(bl2),512k(u-boot-env),2048k(factory),2048k(fip),248320k(ubi)
```

对应十六进制布局：

| 分区 | 起始 | 大小 |
|---|---:|---:|
| bl2 | `0x0000000` | `0x0100000` |
| u-boot-env | `0x0100000` | `0x0080000` |
| factory | `0x0180000` | `0x0200000` |
| fip | `0x0380000` | `0x0200000` |
| ubi | `0x0580000` | `0x0f280000`（248320 KiB） |

# WF3000A 纯净固件 —— 构建时打补丁模式

**核心原则：`immortalwrt-mt798x-6.6` 源码仓库保持原样，一个字节都不手动改。**
所有移植改动由 `scripts/prepare-source.py` 在 CI 里、克隆之后、编译之前完成。

## 目录

```
.github/workflows/build.yml     工作流（拉上游源码 → 打补丁 → 编译）
config/wf3000a.config           固件配置（意图清单：只列要的，不列不要的）
custom/wf3000a/
  mt7981b-kst-wf3000a.dts       ①设备树：114MiB 分区 + 端口 + MAC nvmem
  device.mk                     ②镜像定义：机型注册 + IMAGE_SIZE
  02_network                    【参考】打完补丁后的完整文件
  platform.sh                   【参考】打完补丁后的完整文件
scripts/prepare-source.py       ★ 打补丁 + 复核（核心）
scripts/simulate.py             本地模拟运行（不用等 CI）
README.md
```

`custom/wf3000a/` 下的 `02_network` 和 `platform.sh` **只是给你看"补丁长什么样"**，
脚本不会去复制它们——它会自己在源码文件上做插入。

## 脚本做的四件事

| # | 动作 | 目标文件 | 为什么 |
|---|---|---|---|
| 1 | 复制 DTS | `mediatek/dts/mt7981b-kst-wf3000a.dts` | 分区/端口/GPIO/MAC 全靠它 |
| 2 | 追加镜像定义 | `mediatek/image/filogic.mk` | **让 config 符号存在**（见下） |
| 3 | 改网络配置 | `filogic/base-files/etc/board.d/02_network` | 端口划分 + MAC |
| 4 | 改升级脚本 | `filogic/base-files/lib/upgrade/platform.sh` | 打通 sysupgrade |

### 动作 2 为什么最关键

```
filogic.mk 里写 TARGET_DEVICES += kst_wf3000a
        ↓  构建系统扫描时登记
生成符号 CONFIG_TARGET_mediatek_filogic_DEVICE_kst_wf3000a
        ↓
.config 才能选中它
```

**顺序是反的**：先在 makefile 里声明设备，config 符号才存在。
没有动作 2，`.config` 里写 `..._DEVICE_kst_wf3000a=y` 会被 `make defconfig`
**静默丢弃、不报错**——所以 workflow 里有专门的 `required` 检查拦这一条。

### 动作 3 的两处插入性质不同

| 位置 | 形式 | 原因 |
|---|---|---|
| 接口分组 | **续行并入** `kst,wf3000a\|` | 共用的是纯数据 `"lan1 lan2 lan3" wan`，与 DTS 一致 |
| MAC | **独立分支** 硬编码 | 360T7 那行是读取逻辑 `mtd_get_mac_ascii factory lanMac`，我们 factory 是二进制布局，读不到 |

### 动作 4 只改一个函数

padavanonly 6.6 的 `platform.sh` 里，360T7 只出现在 `platform_do_upgrade()`；
`platform_check_image()` 走通用 `*)` 的 `nand_do_platform_check`，已覆盖我们。
脚本不写死"补几处"，而是**数锚点出现几次补几次**，上游改版也不误判。

## 关键数字

| 项 | 原版(248MiB) | 本版(114MiB) |
|---|---|---|
| DTS ubi | `0x0580000 0x0f280000` | `0x0580000 0x07200000` |
| IMAGE_SIZE | `248320k` | `115712k` |
| 总地址空间 | `0x0F800000` | `0x07780000` |

对应 U-Boot `expand(114m)`：
```
nmbm0:1024k(bl2),512k(u-boot-env),2048k(factory),2048k(fip),116736k(ubi)
```
`115712k` 比分区小 1MiB，留给 NMBM 坏块表与 UBI 开销。

## MAC（已实测）

| 位置 | 值 |
|---|---|
| factory `0x4` | `48:DA:35:50:70:F7` |
| factory `0xa` | `48:DA:35:50:70:F8` |
| 机身标签 | `48:DA:35:50:70:F5` |

分配块 **F5=LAN / F6=WAN / F7=2.4G / F8=5G**。
factory 只有两个无线 MAC，以太网 MAC 不在 flash → 硬编码。

## 使用步骤

1. 本目录内容作为一个**独立仓库**上传到 GitHub
2. **Settings → Actions → General → Workflow permissions** 勾 **Read and write**
3. Actions → `Build WF3000A ImmortalWrt 6.6` → **Run workflow**
4. 等 1~2 小时，产物在 Artifacts

找这两个文件（文件名带 `kst_wf3000a` 即说明选对了机型）：
```
immortalwrt-...-mediatek-filogic-kst_wf3000a-squashfs-factory.bin
immortalwrt-...-mediatek-filogic-kst_wf3000a-squashfs-sysupgrade.bin
```

## 刷机

**首次**：U-Boot Web（192.168.1.1，布局选 **114m**）上传 `factory.bin`

**后续升级**：
```sh
sysupgrade -n /tmp/xxx-sysupgrade.bin
```
⚠️ 两个文件不能混：factory.bin 给 U-Boot，sysupgrade.bin 给系统内升级。

**验证 MAC**：
```sh
cat /sys/class/net/lan1/address   # 48:da:35:50:70:f5
cat /sys/class/net/wan/address    # 48:da:35:50:70:f6
```

## 本地试跑脚本（不用等 CI）

```bash
git clone --depth=1 -b openwrt-24.10-6.6 \
  https://github.com/padavanonly/immortalwrt-mt798x-6.6
python3 scripts/prepare-source.py ./immortalwrt-mt798x-6.6
```

通过会打印 13 项 OK；任何一项不符 → `exit 1` 并指明哪条。

## 幂等性

脚本**不能重复运行**——第二次会因 `filogic.mk` 里已有 `kst_wf3000a`
直接报明确错误停下（而不是生成错误固件）。CI 每次都是全新克隆，不会遇到。

## 换上游版本

只改 workflow 里的 `SOURCE_COMMIT` 即可。
若脚本报"找不到锚点"，说明上游改了文件格式，需人工调整 `ANCHOR`
或改用 `custom/wf3000a/` 里的完整文件手工同步。

## 本地模拟运行（推荐先跑一遍）

不用等 CI、不用真编译，几十秒验证整条链路：

```bash
python3 scripts/simulate.py
```

它会：
1. 校验 `prepare-source.py` / `build.yml` / 两个 shell 参考文件的语法
2. 建一棵模拟源码树，跑一遍打补丁，检查补丁位置与续行链
3. 模拟 `make defconfig` 的符号解析，跑硬校验/软校验
4. 跑三个场景：全部可用 / 软项缺失 / 机型符号丢失
5. 检查幂等性（重复运行是否报错停止）

当前状态：**22 项全部通过**。

含 5 个场景：
| 场景 | 期望 | 结果 |
|---|---|---|
| A 全部可用 | 通过 | ✅ |
| B ca-certificates 缺失 | 硬校验仍过，软项 warn | ✅ |
| C device.mk 未追加 | 中止 | ✅ |
| D 中文符号丢失 | 中止 | ✅ |
| D2 兜底 i18n 包缺失 | 不误伤 | ✅ |

## config 是「意图清单」，不是「全量快照」

只写要的，不写不要的：

```sh
CONFIG_PACKAGE_luci=y        # 要
# 不写的包，默认就是不编译
```

原 Anser-y 配置里那种 `# CONFIG_PACKAGE_xxx is not set` 的写法来自
`./scripts/diffconfig.sh` 的输出——那是**给机器读的产物**，不是手写的意图清单。
两者用途不同，不要混用。列一堆排除项只会让文件越来越难维护。

**依赖不需要手写**：`make defconfig` 会沿依赖树自动展开。
只写 `luci`，就会自动补上 uhttpd / rpcd / libuci / libubus / ...

想确认最终编了什么：看 workflow 输出的 `build.diffconfig`，
或刷完后在路由器上跑 `opkg list-installed`。

## 编进固件 vs 事后安装

判断标准（这条决定了 config 里该写什么）：

| 类型 | 编进去？ | 理由 |
|---|---|---|
| `kmod-*` | **必须** | 与内核版本强绑定，事后 `opkg install` 必然失败 |
| `ca-bundle` / `ca-certificates` | **必须** | 否则 opkg 连不上 https 源，「按需安装」本身不成立。SSL 后端（libustream-\*）不用写，opkg 依赖会自动选中一个 |
| 中文界面 `CONFIG_LUCI_LANG_zh_Hans` | **必须** | 明确需求。它不是插件，是 LuCI 的语言配置项，必须编译期确定 |
| `luci` | 建议 | 让机器可用的基础，事后装也行但没必要省 |
| `luci-app-*` | 不必 | 纯脚本/二进制，随时 `opkg install` |
| 界面语言、主题 | 不必 | 同上 |
| `zram-swap` 等小工具 | 不必 | 256MB 内存够用，需要时装 |

所以当前 config 只编这三类：**机型 + HNAT(kmod) + LuCI(含 https 依赖)**。

## 两个常被问到的包（当前都没编进去）

### `luci-app-package-manager` —— LuCI 里的「软件包」页面

| | |
|---|---|
| 是什么 | opkg 的**网页前端**，在 LuCI 里点几下装/删软件 |
| 是基础系统包吗 | **不是**，是 luci-app 类插件 |
| 缺了有什么影响 | LuCI 里没有「软件包」菜单。**不影响**从 SSH 用 `opkg install` |
| 能事后装吗 | 能，纯脚本：`opkg install luci-app-package-manager` |

### `luci-ssl` —— 让 LuCI 走 HTTPS

| | |
|---|---|
| 是什么 | 一个**元包**，拉进 `uhttpd-mod-tls` + 一个 SSL 后端 + `px5g`（生成自签证书） |
| 是基础系统包吗 | **不是** |
| 缺了有什么影响 | LuCI 只能 `http://` 访问。家内局域网无所谓，且自签证书反而会让浏览器弹「不安全」警告 |
| 能事后装吗 | 能，全是用户态：`opkg install luci-ssl` |

⚠️ **别把它和 `ca-bundle` 搞混**：

```
luci-ssl     →  入站 HTTPS：浏览器 ──▶ 路由器管理页
ca-bundle    →  出站 HTTPS：路由器 ──▶ 软件源下载插件
```

方向相反、用途不同。我们**保留**了出站用的 ca-bundle / ca-certificates
（否则 opkg 连不上软件源），**省略**了入站用的 luci-ssl（家内 http 够用）。

两者都不是 `kmod-*`，所以随时能补上。想现在就编进去，把这两行加到
`config/wf3000a.config` 即可：

```sh
CONFIG_PACKAGE_luci-app-package-manager=y
CONFIG_PACKAGE_luci-ssl=y
```

## 以后装插件

```sh
opkg update && opkg install luci-app-xxx
```

常用：
```sh
opkg install luci-app-upnp luci-app-ddns luci-app-wol   # 网络小工具
opkg install luci-i18n-base-zh-cn                        # 中文（若编译时没带上）
opkg install zram-swap                                   # 内存压缩
```

⚠️ 带 `kmod-*` 的包（如 `kmod-wireguard`）必须重新编译后刷入，
事后装会因内核版本不匹配失败。

