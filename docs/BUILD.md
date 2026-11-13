# 从源码到 boot.img（高版本 / A12-A13 同样适用）

## 1. 内核：上游 XNU + 补丁

- **上游 ios-sweet 补丁 0001–0081**：`xnu/upstream-patches/`
- **camellia 补丁 0082–0214**：`xnu/patches/`
- 应用顺序：先 0001–0081，再 0082+（按文件名字典序）。
- CI：`.github/workflows/xnu.yml`，基于 `blacktop/darwin-xnu-build`；
  `MACHINE_CONFIG=SWEET`、`KERNEL_CONFIG=DEVELOPMENT`。
- 产物：`kernel.development.camellia`。

> A12/A13 只影响 Android 底包/LK/boot header，不改变 XNU 补丁本身；
> 但 LK 状态/AVB/boot header 要按目标固件适配。

## 2. 编译 loader（shim）

```bash
cd shim
./build.sh
# 产出 loader.bin.gz
```

`mk_device_boot.py` 用 `--loader` 指定它。

## 3. 打 boot.img

```powershell
python tools\mk_device_boot.py --root ufs `
  --stock-boot <目标固件的原厂 boot.img> `
  --expected-stock-sha256 <该 boot.img 的 SHA256> `
  --dtb <目标固件的 dtb> `
  --kernel <kernel.development.camellia> `
  --adt stock\camellia-fb1.adt `
  --cmdline (Get-Content -Raw stock\cmdline-142.txt) `
  --allow-userdata-write `
  --payload-out payload-143.bin `
  --out boot_iosroot143.img
```

说明：

- `--stock-boot` 只作为 **header/偏移模板**；`--expected-stock-sha256` 防止用错模板。
- `--dtb` 必须是目标固件的 dtb；A11 的 `stock/dtb.bin` 只适用于 A11。
- `--adt` 是平台设备树（camellia 的 CPU/display/PCIe 等），不随 Android 版本变；A12/13 可以先复用
  `stock/camellia-fb1.adt`，不行再由 `tools/mkadt_camellia.py` 重生成。
- 原厂 boot 比默认模板大时加 `--allow-oversize`。
- A12/13 如果启用了 AVB，需要先 `fastboot --disable-verity --disable-verification flash vbmeta <原厂 vbmeta>`。

## 4. 刷机

```bash
fastboot flash boot boot_iosroot143.img
fastboot reboot
# 换 iOS root 时：
fastboot -S 64M flash userdata iosroot-20H392.img
```

只写 `boot_a`；不刷 dtbo/super/modem/persist。

## 5. 目录

- `xnu/upstream-patches/`：0001–0081
- `xnu/patches/`：0082–0214（含 0212/0213/0214 合成触摸/MTK 触摸探针）
- `stock/`：A11 的 dtb、ADT、142 cmdline
- `tools/`：打包/刷机/分析
- `shim/`：loader 源码
- `root/`：iOS root 构建/调整脚本
- `touch/`：触摸参考源码与说明
