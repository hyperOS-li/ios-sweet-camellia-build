# 2026-11-13 触摸：bootloader 判定与固件加载（0217）

## 1. 已确认：SPI 通了

147（0216，cannon GPIO 模型 + 上升沿前采样）实机日志：

```
SWEET TOUCH MTK: pass 13 trim 0c0000726603 irq=0 rst=af host=3b hs=91 page=none
```

- `trim = 0c 00 00 72 66 03`，末三字节 `72 66 03` 命中厂商 trim 表：
  `{0x0C, FF, FF, 0x72, 0x66, 0x03}` → **NT36675_memory_map**（事件页 **0x22D00**，hw_crc=2）；
- 说明：Paris GPIO 模型、bit-bang 时序、页选择/读帧**全部正确**；
- `page=none` 是因为芯片**没有在跑固件**（见下），不是读错。

## 2. 根因：芯片停在 bootloader，固件没下载

厂商 `nt36xxx.c` 的启动流程：

1. `nvt_ts_probe()` → `nvt_eng_reset()` → `nvt_ts_check_chip_ver_trim()`；
2. trim 检查内部先 `nvt_bootloader_reset()`（写 `0x3F0FE = 0x69`，delay 5 ms），**每次开机都把芯片打进 bootloader**；
3. 10 s 后 `Boot_Update_Firmware` → `nvt_update_firmware("nt36672c_tm_01_ts_fw.bin")`：
   - 分节写 ILM/DLM/Info/Overlay 到 SRAM；
   - 设 ILM/DLM 描述符、长度、CRC；
   - 读 `0x3F30E` 置 bit7（BLD CRC 使能）；
   - 写 `EVENT+0x60 = 0`、`EVENT+0x50 = 0xAE`、`0x3F10D = 1`（boot ready）；
   - 轮询 `EVENT+0x60` 到 `0xA0..0xAF`；
4. 固件跑起来后 FWINFO/PID/触摸报文才有效。

iOS 侧从来没有第 3 步，所以 trim 读得到、FWINFO 是垃圾——与 147 的日志完全一致。

## 3. 固件来源：本机原厂 ROM

`C:\rom_a11\…\super.img`（Android sparse，9.1 GB）→

1. `tools/unpack_super.py raw` → `D:\super_camellia.raw`；
2. LP metadata（geometry 在 0x1000，metadata 在 0x3000）解析出 `vendor_a`（687.6 MiB，extent @ sector 458752）；
3. `tools/unpack_super.py extract super.raw vendor_a vendor.img`，ext4 magic `53 ef` 验证通过；
4. 7-Zip 从 `vendor.img` 提取 `firmware/nt36672c_tm_01_ts_fw.bin`。

固件信息：

| 项 | 值 |
|---|---|
| 文件 | `nt36672c_tm_01_ts_fw.bin` |
| 大小 | 139,264 B |
| SHA256 | `B7756699088AA5A5E31B9DF1F93D0A0147F3F003AA9E87F691875922503CE481` |
| 头 | `u32@0 = 0x100` |
| 结束标志 | `"NVT"` @ 0x1FFFD（= dmesg 的 `fw_need_write_size=131072`） |
| 元数据 @0x19F30 | `12 ED 12 24 38 04 60 09` → fw 18、18×36、1080×2400 |
| 分节 | ILM(src 0x100, sram 0x0, size 0x12577)、DLM(0x180D0/0x27110/0x43B7)、Info-12(0xFF)、Overlay-0/1 |

## 4. 0217：固件加载器

- 固件以 hex 字符串编进内核：`bsd/dev/sweet_nvt_fw_hex.h`（285 KB 文本，2186 行）；
- `sweet_touch_mtk.c` 增加：
  - `fw_decode()`/`fw_parse()`（按厂商头格式解析 17 个分节）；
  - `nvt_write()`/`nvt_write_section()`（≤64 B、不跨 128 B 页边界）；
  - `nvt_boot_fw()`：bootloader reset → 读旧 trim 校验 → 写分节 → 设 ILM/DLM 描述符/长度/CRC
    → BLD CRC 使能 → `0x22D60=0`、`0x22D50=0xAE`、`0x3F10D=1` → 轮询 reset state
    → 读 FWINFO（0x22D78）和 PID（0x22D9A）；
  - boot-arg `sweetmtkfw=1` 才执行固件下载（默认关闭，避免影响只读探针）。

## 5. 下一次测试（148）

cmdline 在 147 的基础上加 `sweetmtkfw=1`。预期日志：

```
SWEET TOUCH MTK: fw sections=17 ilm=0x0/0x12577 dlm=0x27110/0x43b7
SWEET TOUCH MTK: fw old trim ...
SWEET TOUCH MTK: fw written, setting crc banks
SWEET TOUCH MTK: fw reset state=a? tries=N
SWEET TOUCH MTK: fw info 12 ed 12 24 0438 0960
SWEET TOUCH MTK: fw pid=5949
SWEET TOUCH MTK: fw boot result=0
```

- 若 `fw reset state` 不是 `A0..AF`：查 CRC/描述符/分节地址；
- 若 `fw info` 仍不对：查事件页是否应为 0x22D00（trim 表已确认）或固件元数据偏移；
- 成功后进入报文读取（下一步 0218：65 B 报文 → 10 指 → `sweet_hid_touch()`）。
