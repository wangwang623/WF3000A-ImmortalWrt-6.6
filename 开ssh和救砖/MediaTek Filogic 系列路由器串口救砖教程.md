前言：
感谢蝈蝈大佬开发的 mtk_uartboot 工具
适用系列：MediaTek MT7622、MT7981、MT7986、MT7988 SoC

准备刷机工具
二进制：
地址：https://github.com/981213/mtk_uartboot/releases
Windows 用户选择 mtk_uartboot-x86_64-pc-windows-msvc.zip
Linux 用户选择 mtk_uartboot-x86_64-unknown-linux-gnu.tar.gz
手动编译：
sudo apt install aptitude
sudo aptitude install rust-all
git clone https://github.com/981213/mtk_uartboot && cd mtk_uartboot
cargo build --verbose --locked --release --target x86_64-unknown-linux-gnu
目标文件路径：target/x86_64-unknown-linux-gnu/release/mtk_uartboot
编译ATF (RAM BOOT)
准备开发环境：
git clone -b mtksoc --single-branch https://github.com/mtk-openwrt/arm-trusted-firmware
cd arm-trusted-firmware
对代码进行patch用于支持ram boot
--- a/plat/mediatek/apsoc_common/bl2/Config-uart_dl.in
+++ b/plat/mediatek/apsoc_common/bl2/Config-uart_dl.in
@@ -10,7 +10,7 @@ config _RAM_BOOT_RAM_BOOT_UART_DL
     bool "Enable RAM boot UART download support"
     depends on _BOOT_DEVICE_RAM
     depends on !_RAM_BOOT_DEBUGGER_HOOK
-    depends on _INTERNAL
+    # depends on _INTERNAL
     default n
  
 # Makefile options
然后就可以选择构建目标了（以 MT7981 DDR3 内存为例）：
 
在 Advanced boot device configuration 里面选中 Enable RAM boot UART download support
 
注意：MT7981B 需要在 Advanced DRAM configurations 里面选择内存封装为 BGA，默认是 QFN，不改此项刷入 bl2 必砖
 
开始构建：make CROSS_COMPILE=aarch64-linux-gnu-
目标文件：build/mt7981/release/bl2.bin
开始救砖
我这里提供一份预先构建的 bl2：https://www.lanzouw.com/ioTYu1pvi23g
涵盖 MT7622、MT7981、MT7986 DDR3/4 MT7988 没试过，不知道
以 MT7981 DDR3 内存为例开始救砖：
Linux 上：
注意：此时不能有其它程序访问/dev/ttyUSB0
其中 fip.bin 为某个机子的 uboot，/dev/ttyUSB0 为连接至路由器 UART 的串口
sudo ./mtk_uartboot -s /dev/ttyUSB0 -p mt7981/mt7981-ddr3-bl2.bin -a -f fip.bin --brom-load-baudrate 921600 --bl2-load-baudrate 1500000
如果你的串口承受不了 1500000 的波特率，那么请将 --brom-load-baudrate 和 --bl2-load-baudrate 设置成 115200
sudo ./mtk_uartboot -s /dev/ttyUSB0 -p mt7981/mt7981-ddr3-bl2.bin -a -f fip.bin --brom-load-baudrate 115200 --bl2-load-baudrate 115200
 
打印 Received FIP 后使用串口工具访问串口，此时会打印 Hit any key to stop autoboot
操作上下键分别选择 Upgrade ATF BL2 和 Upgrade ATF FIP 即可完成救砖
 
Windows 上：
注意：此时不能有其它程序访问COM4
其中 fip.bin 为某个机子的 uboot，COM4 为连接至路由器 UART 的串口
.\mtk_uartboot.exe -s COM4 -p .\mt7981\mt7981-ddr3-bl2.bin -a -f fip.bin --brom-load-baudrate 921600 --bl2-load-baudrate 1500000
如果你的串口承受不了 1500000 的波特率，那么请将 --brom-load-baudrate 和 --bl2-load-baudrate 设置成 115200
.\mtk_uartboot.exe -s COM4 -p .\mt7981\mt7981-ddr3-bl2.bin -a -f fip.bin --brom-load-baudrate 115200 --bl2-load-baudrate 115200
打印 Received FIP 后使用串口工具访问串口，操作上下键进入 Boot Menu 完成救砖
 
本文作者暗云，https://www.cnblogs.com/p123/p/18046679


