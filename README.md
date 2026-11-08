# ios-sweet-camellia

Redmi Note 10 5G（**camellia** / MediaTek **MT6833**）上跑 iOS 16.7.16（20H392）用户态的 bring-up 工程。

> 现状（2026-11-08）：**整屏 iOS 锁屏已经显示**（时间/日期/相机/Home 条），SpringBoard 正常出 UI；
> **触摸尚未打通**（M5），锁屏不能上滑解锁，Settings 还进不去。

## 仓库内容

```
xnu/patches/        我们改的 XNU 内核补丁（0082–0211）
tools/              打包 / 刷机 / 分析工具
  mk_device_boot.py   boot.img 打包（loader + kernel + ADT + cmdline）
  bootimg.py          Android boot image 封装
  mkadt_camellia.py   设备树（ADT）
  fetch_kernel.py     从 GitHub Actions artifact 拉内核
  gh_commit.py        通过 Git Data API 提交补丁
  video_frames.py     手机视频抽帧（实验分析）
  ...
shim/               boot.img 里的 loader 源码（start.S / loader.c / ufs.c / mmu.c / ...）
root/               iOS root 构建/调整脚本（mkiosroot.sh / sweet-root-tweaks.py）
docs/               构建与刷机文档
```

## 构建链

1. **内核**：上游 XNU + `xnu/patches/` 按文件名字典序应用，GitHub Actions 构建
   `kernel.development.camellia`。
2. **boot.img**：`tools/mk_device_boot.py` 把
   - kernel 槽：`shim` loader
   - ramdisk 槽：SWEETXNU payload（内核 + ADT + cmdline）
   - dtb 槽：原厂 dtb
   打成 `boot_iosrootNNN.img`。
3. **iOS root**：`root/mkiosroot.sh` 从 iPhone10,3 16.7.16 (20H392) IPSW 构建 16 GiB HFS+ 镜像，
   再用 `root/sweet-root-tweaks.py` 打迁移/Setup/容器/音频等补丁。

## 刷机

只写 `boot_a`（iOS root 在 `userdata`）：

```bash
fastboot flash boot boot_iosroot142.img
fastboot reboot
```

刷 userdata（换 root 时）：

```bash
fastboot -S 64M flash userdata iosroot-20H392.img
```

LK 的 `max-download-size` 是 128 MiB，所以必须 `-S 64M`；最后 4.8 MB 会报
`sparse image write failure`，但前 184 块足够 HFS 挂载。

## 关键点

- 只写 `boot_a`；`boot_b`、dtbo、super、modem、persist 不动。
- 这台 LK 不支持 `fastboot boot`，必须 `flash boot` 后重启。
- 16 GiB userdata root 不适合放 Git；用 Release/网盘单独分发。
- 触摸驱动 `shim` 之外的 `sweet_touch.c` 目前是 Qualcomm Sweet 的 GCC/TLMM/SE7 实现，
  **不适用于 MT6833**；M5 需要写 MTK 的 I2C/GPIO（或 bit-bang）触摸驱动。

## 状态

| 里程碑 | 状态 |
|---|---|
| M0 工具链 | ✅ |
| M1 显示 | ✅ |
| M2 XNU | ✅ |
| M3 UFS + iOS root + launchd | ✅ |
| M4 锁屏/桌面 | ✅（2026-11-08） |
| M5 触摸/设置 | ⛔ 触摸驱动待写 |

## 许可 / 来源

内核补丁基于 Apple 开源的 XNU；loader/工具参考
[dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet)。
iOS 用户态（SpringBoard/Settings）是 Apple 闭源二进制，本仓库不分发。
