# stock（A11 / V12.5.9.0 参考资源）

- `dtb.bin`：camellia A11 原厂 dtb（158,118 B）。A12/A13 请换成目标固件的 dtb。
- `camellia-fb1.adt`：camellia 平台 ADT（4,528 B），由 `tools/mkadt_camellia.py` 生成；
  Android 版本一般不影响，可先复用。
- `cmdline-142.txt`：142 的 XNU cmdline（静音 + native + window200 + hz60）。

## A12/A13 怎么用

- `--stock-boot` 用目标固件的原厂 `boot.img`（当 header/偏移模板）；
- `--dtb` 用目标固件的 dtb；
- `--expected-stock-sha256` 传该 boot.img 的 SHA256；
- `--adt` 先复用 `camellia-fb1.adt`；
- 如果 AVB 拦，先 `fastboot --disable-verity --disable-verification flash vbmeta <原厂 vbmeta>`。
