# camellia 触摸（M5）现状与修复方向

## 现状

- iOS 锁屏已经整屏显示（2026-11-08），SpringBoard 正常。
- **触摸无反应**。
- 内核里现有的触摸驱动是 `bsd/dev/sweet_touch.c`，来自上游 ios-sweet 的
  **高通 Sweet（SM7150）** 实现：GCC / TLMM / SE7（Qualcomm I2C）。
- camellia 是 **MediaTek MT6833**，没有这些 Qualcomm 寄存器，所以启动日志停在：

```
SWEET TOUCH: mapping GCC/TLMM/SE7
SWEET TOUCH: gcc vote=... rcg=... cfg=... dfs=...
SWEET TOUCH: RCG update timeout
```

这不是小 bug，是**在错误的 SoC 上访问不存在的 GCC/SE7**。

## 需要的信息（从原厂 MT6833 dtb / Android 拿）

1. 触摸 IC 型号：Novatek NT36xxx / FocalTech FT3xxx / Goodix GT9xxx / 其他；
2. I2C 总线号与 7-bit 地址（如 0x62 / 0x38 / 0x5d）；
3. INT（中断）和 RST（复位）GPIO 号；
4. 供电轨（VDD / VIO / AVDD）及是否需要 PMIC 开启；
5. 分辨率与坐标范围（面板 1080×2400）。

## 推荐实现

**方案 A（推荐，绕开 MTK I2C 时钟）：GPIO bit-bang I2C**

- 用 MTK GPIO 的输入/输出/上拉模拟 SCL/SDA；
- 只依赖 GPIO 寄存器，不碰 MTK I2C 控制器和时钟；
- 速度 10–50 kHz 足够触摸轮询；
- 读触摸报告，解析后调用现有的 HID 入口：

```c
// SweetHID.cpp
gSweetTouch->touch(finger, down != 0, x, y);   // 或 sweet_touch_report(...)
```

**方案 B：MTK I2C 控制器**

- 需要 MTK I2C 寄存器、时钟/复位、DMA；
- 工作量大，容易卡在时钟/电源。

## 坐标

- 面板 1080×2400；把触摸 IC 的原始坐标线性映射到 1080×2400；
- 注意 Y 轴方向、边缘裁剪；
- 先在全屏画一个已知坐标的测试点，确认映射。

## 构建/刷机

- 内核补丁：`xnu/patches/`（0082–0211），CI 构建；
- boot 打包：`tools/mk_device_boot.py`；
- 刷入：`fastboot flash boot boot_iosrootNNN.img`；
- 只写 boot_a，userdata 是 iOS root。

## 相关文件

- `touch/sweet_touch.c` — 当前（Qualcomm）触摸驱动全文
- `touch/SweetHID.cpp` — HID 事件服务，`SweetHIDTouch::touch()` 是注入入口
- `ios-sweet/xnu/patches/0060-sweet-keys-touch-drivers.patch`
- `ios-sweet/xnu/patches/0062-sweet-touch-power-battery.patch`
- `xnu/patches/` — 我们 0082–0211 的补丁（含显示/窗口/HID 相关）
