# KST-WF3000A —— 128MB / NMBM 114MiB 布局
# 用法：把本文件内容追加到 openwrt/target/linux/mediatek/image/filogic.mk 末尾
#
# 与原 Anser-y 版本的差异：
#   DEVICE_VARIANT  248MiB -> 114MiB
#   IMAGE_SIZE      248320k -> 115712k
#
# 115712k = 0x7100000，比 DTS 里 ubi 分区 0x7200000(116736k) 少 1MiB，
# 预留给 NMBM 坏块表 / UBI 开销。这个数值与恩山「小圆」114m 实测可启动的配置一致。
# 若你确认自己 flash 余量充足，可改成 116736k（等于 ubi 分区满打满算）。

define Device/kst_wf3000a
  DEVICE_VENDOR := KST
  DEVICE_MODEL := WF3000A
  DEVICE_VARIANT := NMBM 114MiB layout
  DEVICE_DTS := mt7981b-kst-wf3000a
  DEVICE_DTS_DIR := ../dts
  SUPPORTED_DEVICES := kst,wf3000a
  UBINIZE_OPTS := -E 5
  BLOCKSIZE := 128k
  PAGESIZE := 2048
  IMAGE_SIZE := 115712k
  KERNEL_IN_UBI := 1
  IMAGES += factory.bin
  IMAGE/factory.bin := append-ubi | check-size $$$$(IMAGE_SIZE)
  IMAGE/sysupgrade.bin := sysupgrade-tar | append-metadata
endef
TARGET_DEVICES += kst_wf3000a
