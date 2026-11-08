# iOS rootfs（camellia）WSL2 流水线 · 2026-10-30 备料

> 目标：从 iPhone X（d22ap, iPhone10,3）**iOS 16.7.16 / 20H392** 的 IPSW 生成一个
> 16 GiB journaled HFS+ 根卷（含 OS rootfs + SystemOS/AppOS cryptex + dyld shared cache 硬链接 +
> system keybag），写进 `userdata` 后用 `rd=ufsroot0` 启动真 launchd。
> 本文只讲 **host 侧构建**；刷 `userdata` 是破坏性操作，必须用户单独批准（见
> `15_N2b真机用户态与GPT取证_2026-10-30.md` §8）。

## 0. 为什么必须 Linux，以及本机的两个坑

- APFS 镜像（iPhone X 16.7.x）里的文件是 **LZBITMAP** 压缩，只有 **apfs-fuse** 能读；
  HFS+ 的写入要 `mount -t hfsplus`（Linux macOS 都行，本机是 Windows）。
- **坑 1：WSL 功能默认没开**。2026-10-30 20:22 已执行
  `Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart`
  和 `VirtualMachinePlatform`，**需要重启 Windows 一次生效**（重启会结束当前 DSH 会话）。
- **坑 2：WSL2 自带内核没有 hfsplus**。已核对 `microsoft/WSL2-Linux-Kernel`
  的 `Microsoft/config-wsl`（linux-msft-wsl-6.6.y）：`# CONFIG_HFSPLUS_FS is not set`。
  → 要用 `tools/wsl_kernel_hfsplus.ps1` 编一个**只多开 hfsplus** 的 WSL2 内核，再用
  `%USERPROFILE%\.wslconfig` 指过去。

## 1. 材料

| 项 | 值 |
|---|---|
| IPSW | `iPhone10,3,iPhone10,6_16.7.16_20H392_Restore.ipsw`，**6,171,035,648 B** |
| URL | `https://updates.cdn-apple.com/2025FallFCS/fullrestores/122-77006/1158E502-D76D-4226-AA06-ED5E30CD1111/iPhone10,3,iPhone10,6_16.7.16_20H392_Restore.ipsw` |
| 本地 | `D:\ios-sweet-ipsw\iPhone10,3_16.7.16_20H392_Restore.ipsw`（下载中，2026-10-30 20:24 已 ~20%） |
| 解读 | zip 里 3 个 `.dmg`：OS rootfs ~4.73 GB（UDIF）、SystemOS ~2.74 GB（raw APFS cryptex）、AppOS ~10 MB |

**Apple 的东西不进仓库、不外发**（红线）。

## 2. 步骤（重启 Windows 之后按顺序跑）

```powershell
cd C:\Users\Administrator\Desktop\deepseek刷机\红米Note10_MTK_iOS

# ① Ubuntu + 磁盘迁到 D: + apfs-fuse/hfsprogs/rsync
powershell -ExecutionPolicy Bypass -File tools\wsl_setup_ubuntu.ps1

# ② 编一个开 hfsplus 的 WSL2 内核（WSL 磁盘在 D:，不影响 C:）
powershell -ExecutionPolicy Bypass -File tools\wsl_kernel_hfsplus.ps1

# ③ 抽 dmg → 拷进 WSL → mkiosroot.sh → 拿回 16 GiB HFS+ 镜像
powershell -ExecutionPolicy Bypass -File tools\prepare_iosroot.ps1
```

各步细节：

1. **`wsl_setup_ubuntu.ps1`**
   - 检查 WSL/VMP 特性是否已生效（没生效就提示先重启）；
   - 若 `wsl --version` 不可用（22H2 自带的是老 wsl.exe），先 `winget install -e --id Microsoft.WSL` 装新版；
   - `wsl --install -d Ubuntu --no-launch --web-download`（失败再退回 Store 路线）；再
     `wsl --export` + `--unregister` + `--import D:\wsl\Ubuntu` 把磁盘搬到 D:
     （**C: 只剩 18.4 GB，装不下 16 GB 镜像 + 7.4 GB DMG**）；
   - `apt-get install hfsprogs rsync`；`apfs-fuse` 有包就装，没有就源码编到 `/opt/apfs-fuse`。
2. **`wsl_kernel_hfsplus.ps1`**
   - 按 `uname -r`（如 `6.6.87.2-microsoft-standard-WSL2`）自动选
     `linux-msft-wsl-6.6.y` 分支；`cp Microsoft/config-wsl .config`；
     `scripts/config --enable HFSPLUS_FS HFSPLUS_FS_POSIX_ACL NLS_UTF8`；`make -j$(nproc)`；
   - bzImage → `D:\wsl\kernel-hfsplus`，写 `%USERPROFILE%\.wslconfig`（**原文件先备份**），
     `wsl --shutdown` 后验证 `grep -w hfsplus /proc/filesystems`、`losetup -f`、`/dev/fuse`；
   - 回退：删掉 `.wslconfig`，`wsl --shutdown`。
3. **`prepare_iosroot.ps1`**
   - `tools/extract_ipsw_dmg.py`：按大小挑 OS/SystemOS/AppOS（也可 `--os/--sysos/--appos` 指定），
     断点续抽；
   - 校验 distro：`apfs-fuse mkfs.hfsplus fsck.hfsplus rsync` + hfsplus 支持；
   - 把 3 个 dmg 与 `third_party/ios-sweet/tools/mkiosroot.sh` 拷进 `/root/iosroot/`，
     以 root 跑 `mkiosroot.sh ... /root/iosroot/iosroot-20H392.img 16G`，日志 `mkiosroot.log`；
   - 拷回 `D:\ios-sweet-ipsw\iosroot\iosroot-20H392.img`，打印大小 + sha256。

## 3. 之后（破坏性，需用户批准）

```powershell
# 手机会被清空：userdata 里是 Android 的加密 f2fs，这一步会写掉它
fastboot -S 512M flash userdata D:\ios-sweet-ipsw\iosroot\iosroot-20H392.img
fastboot flash boot payload\out\n2\boot_n2b_gpt.img   # 或专门的 rd=ufsroot0 内核（0110-0114 版）
```

恢复路径：官方 ROM 的 `flash_all_except_data_storage.bat`（刷回系统），再 `fastboot format userdata`
（或整包 `flash_all`）。**刷前确认 fastboot 可进、官方 boot 哈希已核。**

## 4. 耗时预估（本机）

| 步骤 | 时间 |
|---|---|
| IPSW 下载（6.17 GB @ ~10 MB/s） | ~10 分钟 |
| 抽 3 个 dmg（6 GB→7.4 GB 写盘） | ~3 分钟 |
| WSL 安装 + 迁 D: + apt | ~10 分钟 |
| apfs-fuse 源码编译（若 apt 没有） | ~5 分钟 |
| WSL2 内核编译（hfsplus） | 15–30 分钟 |
| `mkiosroot.sh`（解 12 GB APFS、rsync -aH、建硬链接、fsck） | 1–3 小时 |
| 刷 userdata（16 GiB，sparse 512M） | 10–30 分钟 |

## 6. 实际实施记录（2026-10-30 晚）

| 步骤 | 结果 |
|---|---|
| WSL/VMP 特性 | 20:22 启用（`-NoRestart`）→ 20:42 重启生效；`HypervisorPresent=True`，Ryzen 5 5600 虚拟化全支持 |
| 现代 WSL | `C:\Program Files\WSL\wsl.exe`（WSL 3.0.1.0 / 内核 **6.18.40.1-1**）。**System32 的 inbox wsl.exe 没有内核**（`wsl --status` 报"未找到 WSL 2 内核"），脚本必须把 `C:\Program Files\WSL` 放到 PATH 最前 |
| 装 Ubuntu | **Store / `wsl --install` / winget 全部失败**（GitHub releases 被墙：`InternetOpenUrl failed 0x80072efd`；ghproxy 镜像只有 4–36 KB/s）。改用 Canonical 的 WSL rootfs：`https://cloud-images.ubuntu.com/wsl/releases/24.04/current/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz`（356,739,129 B，sha256 `8251E27F…15EA`，1.1 MB/s）→ `wsl --import Ubuntu D:\wsl\Ubuntu <tar> --version 2`（37 s） |
| curl 坑 | Windows schannel 的吊销检查离线会报 `CRYPT_E_REVOCATION_OFFLINE`，下载一律加 `--ssl-no-revoke` |
| WSL 磁盘 | `D:\wsl\Ubuntu\ext4.vhdx`（D: 267 GB 空闲；C: 只剩 18 GB，不能用默认位置） |
| 工具 | `hfsprogs`（`mkfs.hfsplus`/`fsck.hfsplus`）、`rsync`、`build-essential`、`cmake`、`git` 装好；**`apfs-fuse` 不在 Ubuntu 24.04 仓库**，源码编（`git clone --recursive`，必需 `3rdparty/lzfse` 子模块）→ `/usr/local/bin/apfs-fuse` |
| APFS 验证 | ✅ OS dmg：`/root/sbin/launchd` = **541,600 B root:root 0755**；SystemOS dmg：`/root/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64*` 齐全（挂载/列目录 4 s） |
| dmg 进 WSL | 7,473,376,620 B 三个 dmg 已拷进 `/root/iosroot/dmg`（9p 拷贝 ~15 MB/s，479 s） |
| hfsplus 内核 | WSL 6.18 的 `Microsoft/config-wsl` 里 `# CONFIG_HFSPLUS_FS is not set`、模块也没带 → **已编好**：源码 `git clone --depth 1 -b linux-msft-wsl-6.18.y`（2.4 GB），开 `HFSPLUS_FS/HFSPLUS_FS_POSIX_ACL/NLS_UTF8`、关 `DEBUG_INFO_BTF/WERROR`，`make -j12` 约 22 分钟；bzImage `D:\wsl\kernel-hfsplus`（13,841,408 B，sha256 `17AB86F8…FD436`），`.wslconfig` 指过去后 `uname -r = 6.18.58.1-microsoft-standard-WSL2+`，`/proc/filesystems` 有 **hfsplus**，`/dev/loop0`、`/dev/fuse` 正常 |
| mkiosroot | 进行中（`tools\prepare_iosroot.ps1 -SkipExtract`）：apfs-fuse 挂 3 个 dmg → `mkfs.hfsplus -s -J` 16 GiB → `rsync -aH --numeric-ids` 12 GB → 硬链接 dyld cache → keybag/logd 目录 → `fsck.hfsplus`，产物拷回 `D:\ios-sweet-ipsw\iosroot\iosroot-20H392.img` |

**网络现实（重要）**：GitHub 的 codeload/release 下载很慢（~38 KB/s），但 **git 协议可用**（`ls-remote` 5 s、clone 2.4 GB 几分钟），
所以内核源码走 `git clone --depth 1`；Apple CDN 很快（12 MB/s）；Ubuntu archive 0.2–2 MB/s。

## 5. 已知风险

- apfs-fuse 对 16.7.16 镜像的兼容性（上游在 Debian/Arch 上跑通过；Ubuntu 24.04 版 apfs-fuse 版本较旧，必要时用源码分支）；
- Ubuntu 版 `mkfs.hfsplus`（hfsprogs 332）与上游 macOS 工具行为差异 —— 上游已用同一套 Linux 工具验证过；
- LZBITMAP 解压后 12 GB 写入 HFS+ 时 inode/attrib 表压力大，`fsck.hfsplus -f -y` 可能要跑几分钟；
- 若 WSL2 自定义内核起不来：删 `.wslconfig` + `wsl --shutdown` 即可回到微软内核（数据不丢）。
